import { get, post, query } from "../api.js";
import { external, h, mount } from "../dom.js";
import { T, count, errorText, pct, timeAgo } from "../i18n.js";
import { icon } from "../icons.js";
import {
  alertBox, busy, button, empty, input, pageHead, segmented, select, skeletonGrid, subTabs, toast, toggle,
} from "../ui.js";
import { capabilityCard } from "../components/capability.js";
import { itemCard } from "../components/item-card.js";
import { openItem } from "../components/item-drawer.js";
import { opportunityCard } from "../components/opportunity.js";

const TABS = [
  ["finds", "Находки", "explore"], ["capabilities", "Способности", "layers"], ["authors", "Авторы", "star"],
  ["briefings", "Брифинги", "bolt"], ["radar", "Общий радар", "radar"],
];

export async function render(root, [tab = "finds"] = []) {
  const body = h("div");
  mount(root,
    pageHead("Обзор", "Всё, что нашёл сбор, — не только для ваших проектов."),
    subTabs(TABS.map(([key, label, iconName]) => [key, label, `#/explore/${key}`, iconName]), tab),
    body);
  const views = { finds, capabilities, authors, briefings, radar };
  await (views[tab] || finds)(body);
}

// ---------- finds ----------

const state = { bucket: "fast_growing", source: "", aiOnly: false };

async function finds(body) {
  const grid = h("div");
  const load = async () => {
    mount(grid, skeletonGrid(9));
    try {
      const items = await get(`/discover${query({ bucket: state.bucket, source: state.source, ai_only: state.aiOnly, limit: 48 })}`);
      mount(grid, items.length ? h("div", { class: "grid" }, items.map((i) => itemCard(i)))
        : empty("explore", "Здесь пусто", "Попробуйте другой источник или запустите сбор."));
    } catch (error) {
      mount(grid, alertBox("error", "Не удалось загрузить", errorText(error)));
    }
  };
  const source = select([["", "Все источники"], ...Object.entries(T.source)], state.source, {
    "aria-label": "Источник", class: "select select-auto", onChange: () => { state.source = source.value; load(); },
  });
  mount(body,
    addUrlBar(),
    h("div", { class: "toolbar" },
      segmented([["fast_growing", "Быстро растут", "bolt"], ["new", "Новые", "sparkle"], ["popular", "Популярные", "star"], ["experimental", "Эксперименты", "cube"]],
        state.bucket, (v) => { state.bucket = v; load(); }, { label: "Подборка" }),
      source,
      toggle("Только оценённые AI", state.aiOnly, (v) => { state.aiOnly = v; load(); })),
    grid);
  await load();
}

function addUrlBar() {
  const field = input({ placeholder: "Нашли что-то сами? Ссылка на GitHub (репозиторий или автора), Hugging Face, arXiv, npm или PyPI", "aria-label": "Ссылка" });
  const result = h("div");
  const add = button("Добавить на радар", { kind: "primary", iconName: "plus" });
  const submit = () => {
    if (!field.value.trim()) return field.focus();
    return busy(add, async () => {
      try {
        const item = await post("/items/add-url", { url: field.value.trim() });
        field.value = "";
        if (item.kind === "author") {
          toast(item.created ? `Автор ${item.login} добавлен в наблюдение` : `Автор ${item.login} уже в наблюдении`, { kind: "success" });
          mount(result);
          return;
        }
        toast(item.created ? "Добавлено на радар со статусом «Наблюдаю»" : "Уже было в базе — обновлено", { kind: "success" });
        mount(result, h("div", { class: "grid section-tight" }, itemCard(item)));
      } catch (error) {
        toast(errorText(error), { kind: "error" });
      }
    }, "Читаю… (AI — до минуты)");
  };
  add.addEventListener("click", submit);
  field.addEventListener("keydown", (e) => e.key === "Enter" && submit());
  return h("div", { class: "addbar-wrap" }, h("div", { class: "addbar" }, icon("link", { size: 16 }), field, add), result);
}

// ---------- capabilities ----------

async function capabilities(body) {
  const search = input({ type: "search", placeholder: "Поиск способности: browser automation, local inference…", "aria-label": "Поиск" });
  let multi = true;
  const grid = h("div");
  let timer;
  const load = async () => {
    mount(grid, skeletonGrid(6));
    try {
      const rows = await get(`/capabilities${query({ q: search.value.trim(), min_items: multi ? 2 : 1 })}`);
      mount(grid, rows.length ? h("div", { class: "grid" }, rows.map(capabilityCard))
        : empty("layers", "Ничего не нашлось", "Уберите фильтр «у 2+ элементов» или запустите сбор."));
    } catch (error) {
      mount(grid, alertBox("error", "Не удалось загрузить", errorText(error)));
    }
  };
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); });
  const merge = button("Объединить синонимы (AI)", { iconName: "sparkle" });
  merge.addEventListener("click", () => busy(merge, async () => {
    try {
      const res = await post("/capabilities/consolidate");
      toast(res.skipped ? "Слишком мало способностей для объединения" : `Объединено групп: ${res.groups}, убрано дублей: ${res.removed}`, { kind: "success" });
      load();
    } catch (error) {
      toast(errorText(error), { kind: "error" });
    }
  }, "Объединяю…"));
  mount(body,
    h("p", { class: "intro muted small" }, "Что умеют найденные инструменты: способности группируют находки по умению и показывают, каким проектам это важно."),
    h("div", { class: "toolbar" }, h("div", { class: "searchbox" }, icon("search", { size: 16 }), search),
      toggle("у 2+ элементов", multi, (v) => { multi = v; load(); }), h("span", { class: "grow" }), merge),
    grid);
  await load();
}

// ---------- authors ----------

let authorState = "watch";

function authorCard(author, onChange) {
  const origin = [
    author.followed && "вы подписаны",
    author.starred_repos > 0 && `★ ${author.starred_repos}`,
    author.added_manually && "добавлен вами",
    author.status === "candidate" && "часто встречается в находках",
    author.account_type === "organization" && "организация",
  ].filter(Boolean);
  const yieldText = author.items
    ? `${count(author.items, "находка", "находки", "находок")} · ${author.relevant} релевантных · ${author.engaged} с вашим участием`
    : "Находок пока нет";
  const actions = [["watch", "Наблюдать"], ["ignore", "Не интересен"]].filter(([s]) => s !== author.status).map(([status, label]) => {
    const b = button(label, { kind: "ghost", size: "sm" });
    b.addEventListener("click", () => busy(b, async () => {
      try {
        await post(`/authors/${author.id}/status`, { status });
        onChange();
      } catch (error) {
        toast(errorText(error), { kind: "error" });
      }
    }));
    return b;
  });
  return h("article", { class: "cap" },
    h("div", { class: "cap-head" }, h("h3", { class: "cap-title" }, external(author.url, author.login)),
      h("span", { class: "count", title: "Приоритет проверки" }, author.priority.toFixed(1))),
    origin.length ? h("div", { class: "tags" }, origin.map((o) => h("span", { class: "tag" }, o))) : null,
    h("div", { class: "muted small" }, yieldText),
    author.top.length ? h("div", { class: "cap-items" }, author.top.map((item) =>
      h("button", { class: "cap-item", type: "button", onClick: () => openItem(item.id) },
        h("span", { class: "cap-item-name" }, item.name),
        item.relevance > 0 && h("span", { class: "faint xs num" }, `рел. ${pct(item.relevance)}`)))) : null,
    h("div", { class: "cap-foot" },
      h("span", { class: "faint xs" }, author.repos_checked_at
        ? `проверен ${timeAgo(author.repos_checked_at)} · репозиториев ${author.repos_seen}` : "ещё не проверялся"),
      h("span", { class: "grow" }), actions));
}

async function authors(body) {
  const grid = h("div");
  const load = async () => {
    mount(grid, skeletonGrid(6));
    try {
      const rows = await get(`/authors?status=${authorState}`);
      const messages = {
        watch: "Пока никого. Укажите GITHUB_LOGIN — ваши подписки и владельцы репозиториев со звёздами появятся после сбора, — или добавьте ссылку на профиль GitHub во вкладке «Находки».",
        candidate: "Кандидаты появляются, когда у одного автора набирается несколько находок, которые AI счёл релевантными вашим проектам.",
        ignore: "Никого не скрыто.",
      };
      mount(grid, rows.length ? h("div", { class: "grid" }, rows.map((a) => authorCard(a, load)))
        : empty("star", "Пусто", messages[authorState]));
    } catch (error) {
      mount(grid, alertBox("error", "Не удалось загрузить", errorText(error)));
    }
  };
  mount(body,
    h("p", { class: "intro muted small" }, "Люди и организации, чьи новые репозитории стоит видеть первыми. Раз в неделю сбор смотрит их профили и выносит на радар несколько лучших: новые — сразу, старые — если набрали звёзды. Выше в списке те, чьи находки чаще оказывались полезными."),
    h("div", { class: "toolbar" }, segmented([["watch", "Наблюдаю"], ["candidate", "Кандидаты"], ["ignore", "Не интересны"]],
      authorState, (v) => { authorState = v; load(); }, { label: "Статус" })),
    grid);
  await load();
}

// ---------- briefings ----------

let briefingState = "active";

async function briefings(body) {
  const list = h("div");
  const load = async () => {
    mount(list, skeletonGrid(3, "skeleton-row"));
    try {
      const rows = await get(`/opportunities?state=${briefingState}`);
      const messages = {
        active: "Активных брифингов нет. AI пишет их после сборов, когда несколько находок вместе — повод пересмотреть решение.",
        faded: "Ничего не угасло: всё подтверждалось недавно.", done: "Пока ничего не отмечено сделанным.", dismissed: "Ничего не скрыто.",
      };
      mount(list, rows.length ? h("div", { class: "stack" }, rows.map((o) => opportunityCard(o, { onChange: load })))
        : empty("bolt", "Пусто", messages[briefingState]));
    } catch (error) {
      mount(list, alertBox("error", "Не удалось загрузить", errorText(error)));
    }
  };
  mount(body,
    h("p", { class: "intro muted small" }, "Сигнал, который возвращается, — тот же брифинг: копятся подтверждения и источники. Переставший возвращаться угасает через две недели. Скрытый не всплывёт снова."),
    h("div", { class: "toolbar" }, segmented([["active", "Активные"], ["faded", "Угасающие"], ["done", "Сделано"], ["dismissed", "Скрытые"]],
      briefingState, (v) => { briefingState = v; load(); }, { label: "Состояние" })),
    list);
  await load();
}

// ---------- overall radar ----------

const ORDER = ["adopt", "trial", "assess", "watch", "new", "hold", "ignore"];

async function radar(body) {
  mount(body, skeletonGrid(4));
  try {
    const { groups, counts } = await get("/radar?per_status=25");
    mount(body,
      h("p", { class: "intro muted small" }, "Общий статус каждой находки. AI может сам предложить максимум «Оценить»; «Пробую» и «Использую» ставите только вы. Решения по конкретным проектам — в рабочем месте проекта."),
      h("div", { class: "board" }, ORDER.map((status) =>
        h("section", { class: `col col-${status}` },
          h("div", { class: "col-head" }, h("i", { class: `dot status-${status}` }), h("span", null, T.status[status]),
            h("span", { class: "count" }, String(counts[status] || 0))),
          h("div", { class: "col-hint" }, T.statusHint[status]),
          (groups[status] || []).length ? groups[status].map((item) =>
            h("button", { class: "mini", type: "button", onClick: () => openItem(item.id) },
              h("span", { class: "mini-title" }, item.name),
              h("span", { class: "mini-meta" }, T.source[item.source] || item.source, item.ai_analyzed ? " · AI" : "",
                item.status_source === "you" ? " · задан вами" : "", ` · рост ${pct(item.momentum_score)}`)))
            : h("div", { class: "col-empty" }, "—"),
          (counts[status] || 0) > 25 && h("div", { class: "faint xs" }, `и ещё ${counts[status] - 25}`)))));
  } catch (error) {
    mount(body, alertBox("error", "Не удалось загрузить радар", errorText(error)));
  }
}
