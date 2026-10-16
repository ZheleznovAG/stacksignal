import { del, get, post, put, query } from "../api.js";
import { external, h, mount } from "../dom.js";
import { T, count, errorText, fmtNum, pct, timeAgo } from "../i18n.js";
import { icon } from "../icons.js";
import { loadProjects } from "../store.js";
import {
  alertBox, busy, button, confirmDialog, empty, input, pageHead, sectionHead, select, skeletonGrid, subTabs, toast, toggle,
} from "../ui.js";
import { statusChip } from "../components/badges.js";
import { itemCard } from "../components/item-card.js";
import { openItem } from "../components/item-drawer.js";
import { openProjectModal } from "../components/project-modal.js";
import { openProjectDirectory } from "../components/project-directory.js";
import { dependencyVersions } from "../components/dependency.js";
import { needsTab } from "../components/needs.js";

export async function render(root, [slug, tab = "relevant"] = []) {
  if (slug) return workspace(root, decodeURIComponent(slug), tab);
  return list(root);
}

// ---------- the list ----------

async function list(root) {
  const actions = [regenerateButton(),
    button("Сканировать папку", { iconName: "folder", onClick: () => openProjectDirectory({ onSaved: () => list(root) }) }),
    button("Новый проект", { kind: "primary", iconName: "plus", onClick: () => openProjectModal(null, { onSaved: () => list(root) }) })];
  const head = pageHead("Проекты", "С чем сопоставляются находки. Конкретные ключевые слова — точнее релевантность.", actions);
  mount(root, head, skeletonGrid(6, "skeleton-tile"));
  let projects, coverage;
  try {
    [projects, coverage] = await Promise.all([loadProjects(true), get("/coverage")]);
  } catch (error) {
    mount(root, head, alertBox("error", "Не удалось загрузить проекты", errorText(error)));
    return;
  }
  const cov = Object.fromEntries(coverage.map((c) => [c.slug, c]));
  const content = h("div");
  const show = (kind) => {
    const visible = projects.filter((p) => !kind || p.kind === kind);
    const groups = Object.keys(T.lifecycle).map((state) => [state, visible.filter((p) => (p.lifecycle_status || p.tier || "active") === state)]);
    mount(content, groups.filter(([, items]) => items.length).map(([state, items]) =>
      h("section", { class: "section" }, sectionHead(T.lifecycle[state], { count: items.length, hint: T.lifecycleHint[state] }),
        h("div", { class: "grid grid-tiles" }, items.map((p) => projectTile(p, cov[p.slug]))))),
      !visible.length && empty("folder", "Контекстов этого типа пока нет"));
  };
  const typeFilter = select([["", "Все типы"], ...Object.entries(T.contextKind)], "", { "aria-label": "Тип контекста",
    onChange: () => show(typeFilter.value) });
  mount(root, head,
    projects.length ? null : empty("folder", "Проектов пока нет", "Создайте первый — находки начнут сопоставляться с ним."),
    h("div", { class: "toolbar" }, typeFilter), content);
  show("");
}

function projectTile(p, cov) {
  const weak = cov && cov.relevant < 3 && p.discovery_allowed;
  return h("a", { class: "tile" + (["dormant", "archived"].includes(p.lifecycle_status) ? " is-muted" : ""), href: `#/projects/${encodeURIComponent(p.slug)}` },
    h("div", { class: "tile-head" }, h("h3", { class: "tile-title" }, p.name), icon("chevron", { size: 16 })),
    h("div", { class: "faint xs" }, T.contextKind[p.kind], " · ", p.discovery_allowed ? "Поиск включён" : "Поиск выключен"),
    h("p", { class: "tile-desc" }, p.description || "Без описания."),
    cov && h("div", { class: "tile-stats" },
      h("span", null, h("b", { class: "num" }, fmtNum(cov.relevant)), "релевантных"),
      h("span", null, h("b", { class: "num" }, fmtNum(cov.found_by_search)), "найдено поиском"),
      h("span", null, h("b", { class: "num" }, fmtNum(cov.queries)), "запросов")),
    Boolean(p.stack?.length || p.needs?.length) && h("div", { class: "faint xs" },
      [p.stack?.length && count(p.stack.length, "технология в стеке", "технологии в стеке", "технологий в стеке"),
        p.needs?.length && count(p.needs.length, "потребность", "потребности", "потребностей")].filter(Boolean).join(" · ")),
    weak && h("div", { class: "tile-warn" }, icon("alert", { size: 13 }), "Мало находок — уточните ключевые слова"));
}

function regenerateButton(slug) {
  const btn = button(slug ? "Пересобрать запросы (AI)" : "Пересобрать запросы (AI)", { iconName: "refresh" });
  btn.title = "Перестроить сгенерированные поисковые запросы (ваши останутся). Шаг AI — до минуты.";
  btn.addEventListener("click", () => busy(btn, async () => {
    try {
      const res = await post("/queries/regenerate", { ai: true, project_slug: slug || null });
      toast(`${res.heuristic} из ключевых слов, ${res.ai} от AI` + (res.ai_error ? ` (AI не ответил: ${res.ai_error})` : ""), { kind: res.ai_error ? "info" : "success" });
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } catch (error) {
      toast(errorText(error), { kind: "error" });
    }
  }, "Пересобираю…"));
  return btn;
}

// ---------- one project's workspace ----------

const TABS = [["relevant", "Релевантное", "sparkle"], ["model", "Модель", "layers"], ["needs", "Потребности", "search"], ["decisions", "Решения", "radar"], ["search", "Поиск", "search"], ["profile", "Профиль", "folder"]];

async function workspace(root, slug, tab) {
  let projects;
  try {
    projects = await loadProjects();
  } catch (error) {
    mount(root, alertBox("error", "Не удалось загрузить проекты", errorText(error)));
    return;
  }
  const project = projects.find((p) => p.slug === slug);
  if (!project) {
    mount(root, empty("folder", "Проект не найден", "Возможно, его удалили.", h("a", { class: "btn btn-secondary btn-sm", href: "#/projects" }, "К проектам")));
    return;
  }
  const base = `#/projects/${encodeURIComponent(slug)}`;
  const body = h("div");
  mount(root,
    h("a", { class: "backlink", href: "#/projects" }, icon("back", { size: 14 }), "Проекты"),
    pageHead(project.name, project.description, [
      h("span", { class: "badge" }, T.contextKind[project.kind]),
      h("span", { class: `badge tier tier-${project.tier}` }, T.lifecycle[project.lifecycle_status]),
      button("Изменить", { iconName: "edit", onClick: () => openProjectModal(project, { onSaved: () => workspace(root, slug, tab) }) }),
    ]),
    subTabs(TABS.map(([key, label, iconName]) => [key, label, `${base}/${key}`, iconName]), tab),
    body);
  const views = { relevant: relevantTab, model: modelTab, needs: needsTab, decisions: decisionsTab, search: searchTab, profile: profileTab };
  await (views[tab] || relevantTab)(body, project, () => workspace(root, slug, tab));
}

const MODEL_STATUS = { existing: "Уже есть", partial: "Частично", planned: "Планируется", unknown: "Неясно" };

async function modelTab(body, project) {
  mount(body, skeletonGrid(3, "skeleton-tile"));
  let model;
  try {
    model = await get(`/projects/${project.id}/model`);
  } catch (error) {
    mount(body, alertBox("error", "Не удалось загрузить модель проекта", errorText(error)));
    return;
  }
  const reload = () => body.isConnected && modelTab(body, project);
  const rethink = button(model.available ? "Переосмыслить проект" : "Построить модель проекта", { kind: "primary", iconName: "sparkle" });
  rethink.addEventListener("click", () => busy(rethink, async () => {
    try {
      const result = await post(`/projects/${project.id}/infer-needs`);
      await loadProjects(true);
      toast(`Модель обновлена: ${result.technical_model?.goals?.length || 0} целей, ${result.technical_model?.subsystems?.length || 0} подсистем, ${result.technical_model?.gaps?.length || 0} пробелов.`, { kind: "success" });
      reload();
    } catch (error) { toast(errorText(error), { kind: "error" }); }
  }, "Анализирую проект…"));

  if (!model.available) {
    mount(body,
      sectionHead("Техническая модель", { actions: rethink }),
      h("p", { class: "muted small section-tight" }, "StackSignal строит её из описания проекта, текущего стека и зависимостей. Ручная разметка технологий для этого не нужна."),
      empty("layers", "Модель ещё не построена", "Опишите проект достаточно подробно и запустите анализ."));
    return;
  }

  const subsystems = Object.fromEntries((model.subsystems || []).map(s => [s.key, s]));
  const goals = Object.fromEntries((model.goals || []).map(g => [g.key, g]));
  const priority = (n) => `приоритет ${n || 2}/3`;
  mount(body,
    sectionHead("Техническая модель", { actions: rethink }),
    model.stale && alertBox("warn", "Модель устарела", "Описание, стек или зависимости проекта изменились после последнего анализа. До переосмысления старые объяснения остаются видимыми, но им нельзя полностью доверять."),
    h("section", { class: "panel panel-pad section-tight" },
      h("p", null, model.summary || "Без сводки."),
      h("p", { class: "faint xs" }, `Построено ${timeAgo(model.updated_at)} · ${model.goals.length} целей · ${model.subsystems.length} подсистем · ${model.gaps.length} пробелов`,
        model.provenance?.provider ? ` · ${model.provenance.provider}` : "")),
    sectionHead("Цели", { count: model.goals.length }),
    model.goals.length ? h("div", { class: "grid grid-tiles section-tight" }, [...model.goals]
      .sort((a, b) => b.priority - a.priority)
      .map(g => h("section", { class: "tile" },
        h("div", { class: "tile-head" }, h("h3", { class: "tile-title" }, g.title), h("span", { class: "badge" }, priority(g.priority))),
        g.outcome && h("p", { class: "tile-desc" }, g.outcome)))) : h("p", { class: "faint small" }, "Явные цели не выделены."),
    sectionHead("Подсистемы", { count: model.subsystems.length }),
    model.subsystems.length ? h("div", { class: "stack section-tight" }, model.subsystems.map(s => h("section", { class: "panel panel-pad" },
      h("div", { class: "panel-head" }, h("h3", { class: "section-title grow" }, s.title), h("span", { class: "badge" }, MODEL_STATUS[s.status] || s.status)),
      s.purpose && h("p", { class: "small" }, s.purpose),
      s.current_capabilities?.length ? h("p", { class: "muted small" }, "Уже умеет: ", s.current_capabilities.join(" · ")) : h("p", { class: "faint small" }, "Подтверждённые возможности не выделены."),
      s.stack_evidence?.length ? h("p", { class: "faint xs" }, "Основание: ", s.stack_evidence.join(" · ")) : null))) : h("p", { class: "faint small" }, "Подсистемы не выделены."),
    sectionHead("Пробелы → что действительно нужно", { count: model.gaps.length }),
    model.gaps.length ? h("div", { class: "stack section-tight" }, [...model.gaps]
      .sort((a, b) => b.priority - a.priority)
      .map(g => h("section", { class: "panel panel-pad" },
        h("div", { class: "panel-head" }, h("h3", { class: "section-title grow" }, g.title), h("span", { class: "badge" }, priority(g.priority))),
        g.why && h("p", { class: "small" }, "Почему это пробел: ", g.why),
        (g.current_state || g.desired_state) && h("p", { class: "muted small" }, `${g.current_state || "?"} → ${g.desired_state || "?"}`),
        g.subsystem_key && subsystems[g.subsystem_key] && h("p", { class: "faint xs" }, `Подсистема: ${subsystems[g.subsystem_key].title}`),
        g.goal_keys?.length && h("p", { class: "faint xs" }, "Цели: ", g.goal_keys.map(k => goals[k]?.title).filter(Boolean).join(" · ")),
        g.needs?.length && h("p", { class: "small" }, "Порождает потребности: ", g.needs.map(n => n.title).join(" · "))))) : h("p", { class: "faint small" }, "Существенных технических пробелов не выделено."),
    model.risks?.length ? [sectionHead("Риски", { count: model.risks.length }), h("div", { class: "stack section-tight" }, model.risks.map(r => h("section", { class: "panel panel-pad" }, h("b", null, r.title), r.why && h("p", { class: "muted small" }, r.why))))] : null,
    h("p", { class: "faint xs" }, "Это производная модель, а не второй источник истины: описание проекта и вручную заданные потребности остаются главными. StackSignal обязан показывать устаревание модели после изменения профиля."));
}

async function relevantTab(body, project) {
  let aiOnly = localStorage.getItem("relevant-ai-only") !== "0";
  const grid = h("div");
  const load = async () => {
    mount(grid, skeletonGrid(6));
    try {
      const items = await get(`/relevant${query({ project_slug: project.slug, ai_only: aiOnly, limit: 60 })}`);
      mount(grid, items.length ? h("div", { class: "grid" }, items.map((i) => itemCard(i)))
        : empty("sparkle", aiOnly ? "AI пока не нашёл ничего релевантного" : "Ничего релевантного",
          aiOnly ? "Выключите «Только оценённые AI», чтобы увидеть совпадения по ключевым словам." : "Уточните ключевые слова или добавьте поисковые запросы."));
    } catch (error) {
      mount(grid, alertBox("error", "Не удалось загрузить", errorText(error)));
    }
  };
  mount(body,
    h("div", { class: "toolbar" },
      toggle("Только оценённые AI", aiOnly, (v) => { aiOnly = v; localStorage.setItem("relevant-ai-only", v ? "1" : "0"); load(); }),
      h("span", { class: "faint xs" }, "Релевантность строится из профиля проекта и его потребностей; 👍/👎 лишь дополнительно калибруют выдачу.")),
    grid);
  await load();
}

const BOARD = ["new", "assess", "trial", "adopt", "watch", "hold", "ignore"];

async function decisionsTab(body, project) {
  mount(body, skeletonGrid(4));
  let groups, revisit, learning;
  try {
    [groups, revisit, learning] = await Promise.all([
      get(`/radar${query({ project_slug: project.slug })}`),
      get("/revisit?notable=true"),
      get(`/radar/preference${query({ project_slug: project.slug, limit: 8 })}`),
    ]);
  } catch (error) {
    mount(body, alertBox("error", "Не удалось загрузить решения", errorText(error)));
    return;
  }
  const mine = revisit.filter((e) => e.project_slug === project.slug);
  const reload = () => decisionsTab(body, project);
  mount(body,
    mine.length ? h("div", { class: "panel section-tight" },
      h("div", { class: "panel-head" }, icon("history", { size: 18 }), h("h2", { class: "section-title" }, "Стоит пересмотреть"),
        h("span", { class: "faint xs" }, "что-то изменилось с момента решения")),
      mine.map((e) => h("button", { class: "change-row linkrow", type: "button", onClick: () => openItem(e.item_id) },
        h("b", null, e.item_name), statusChip(e.status, { decision: true }),
        h("span", { class: "muted small grow" }, e.verdict ? `«${e.verdict}»` : ""),
        h("span", { class: "faint xs" }, timeAgo(e.decided_at))))) : null,
    h("div", { class: "board" }, BOARD.map((status) => column(status, groups[status] || []))),
    learningPanel(learning, reload),
    h("p", { class: "faint xs" }, "Откройте элемент, чтобы записать решение с целью, критерием успеха и вердиктом."));
}

function learningPanel(data, reload) {
  const p = data.profile;
  const context = { project_slug: data.project_slug, project_name: data.project_name };
  const state = p.active
    ? `Профиль активен: ${p.embedded_positive} положительных и ${p.embedded_negative} отрицательных примеров с embeddings.`
    : `До включения профиля: ещё ${p.needed_positive} 👍 и ${p.needed_negative} 👎 с embeddings.`;
  const hint = p.active
    ? "Это вторичный сигнал поверх описания проекта и его потребностей. Он только уточняет порядок похожих кандидатов."
    : "Необязательно: StackSignal уже работает от описания проекта. Несколько явных 👍/👎 позже могут тонко откалибровать похожие находки.";
  return h("section", { class: "panel section" },
    h("div", { class: "panel-head" }, icon("sparkle", { size: 18 }),
      h("div", { class: "grow" }, h("h2", { class: "section-title" }, "Дополнительная калибровка"),
        h("div", { class: "faint xs" }, state))),
    h("p", { class: "muted small" }, hint),
    h("div", { class: "toolbar" },
      h("span", { class: "badge" }, `👍 ${p.positive}`),
      h("span", { class: "badge" }, `👎 ${p.negative}`),
      h("span", { class: "badge" }, p.active ? "обучение включено" : "собираем примеры")),
    data.candidates.length
      ? h("div", null,
        h("p", { class: "faint xs" }, "Кандидаты для быстрой разметки — уже релевантные, но ещё не оценённые для этого проекта:"),
        h("div", { class: "grid" }, data.candidates.map((item) => itemCard(item, {
          context: { ...context, ...item.matched_relevance },
          onVote: () => reload(),
        }))))
      : h("p", { class: "faint small" }, "Сейчас нет новых неоценённых кандидатов для pilot-разметки."));
}


function column(status, items) {
  const label = status === "new" ? "Входящие" : T.decision[status];
  return h("section", { class: `col col-${status}` },
    h("div", { class: "col-head" }, status === "new" ? icon("inbox", { size: 15 }) : h("i", { class: `dot status-${status}` }),
      h("span", null, label), h("span", { class: "count" }, String(items.length))),
    h("div", { class: "col-hint" }, T.decisionHint[status]),
    items.length ? items.slice(0, 30).map((item) => {
      const d = item.decision;
      const m = item.matched_relevance;
      const need = item.matched_need;
      const note = d ? [d.goal && `Цель: ${d.goal}`, d.verdict && `Вердикт: ${d.verdict}`].filter(Boolean).join(" · ") : m?.rationale;
      return h("button", { class: "mini", type: "button", onClick: () => openItem(item.id) },
        h("span", { class: "mini-title" }, item.name),
        need && h("span", { class: "mini-note" }, `${need.readiness === "blocked" ? "Позже" : "Закрывает"}: ${need.title} · приоритет ${need.priority}/3${need.gap?.title ? ` · пробел: ${need.gap.title}` : ""}${need.blocked_by?.length ? ` · сначала ${need.blocked_by.map(x => x.title).join(", ")}` : ""}`),
        note && h("span", { class: "mini-note" }, note),
        h("span", { class: "mini-meta" }, T.source[item.source] || item.source,
          m ? ` · ${pct(m.score)}` : "", ` · рост ${pct(item.momentum_score)}`));
    }) : h("div", { class: "col-empty" }, "—"));
}

async function searchTab(body, project, rerender) {
  mount(body, skeletonGrid(2, "skeleton-row"));
  let queries, coverage;
  try {
    [queries, coverage] = await Promise.all([get("/queries"), get("/coverage")]);
  } catch (error) {
    mount(body, alertBox("error", "Не удалось загрузить запросы", errorText(error)));
    return;
  }
  const mine = queries.filter((q) => q.project_slug === project.slug);
  const cov = coverage.find((c) => c.slug === project.slug);
  const source = select([["github", "GitHub"], ["huggingface", "Hugging Face"], ["arxiv", "arXiv"], ["npm", "npm"]], "github", { "aria-label": "Источник" });
  const text = input({ placeholder: "поисковая фраза, например soft body physics" });
  const add = button("Добавить", { kind: "primary", iconName: "plus" });
  const submit = () => {
    if (!text.value.trim()) return text.focus();
    return busy(add, async () => {
      try {
        await post("/queries", { project_slug: project.slug, source: source.value, text: text.value.trim() });
        toast("Запрос добавлен — сработает при следующем сборе", { kind: "success" });
        rerender();
      } catch (error) {
        toast(error.status === 400 ? "Слишком коротко или такой запрос уже есть" : errorText(error), { kind: "error" });
      }
    });
  };
  add.addEventListener("click", submit);
  text.addEventListener("keydown", (e) => e.key === "Enter" && submit());
  mount(body,
    cov && h("div", { class: "stats stats-wide section-tight" },
      stat(fmtNum(cov.relevant), "сильно релевантных"), stat(fmtNum(cov.found_by_search), "найдено поиском"),
      stat(fmtNum(cov.queries), "активных запросов"), stat(fmtNum(cov.last_new), "нового в последнем сборе")),
    h("p", { class: "intro muted small" }, "Сбор ищет по этим запросам на GitHub, Hugging Face, arXiv и npm. Запрос, который 4 сбора подряд ничего не приносит, отдыхает 2, 4, 8, потом 14 дней."),
    h("div", { class: "query-add" }, source, text, add),
    sectionHead("Запросы", { count: mine.length, actions: regenerateButton(project.slug) }),
    mine.length ? h("div", { class: "panel" }, mine.map((q) => queryRow(q, rerender)))
      : empty("search", "Запросов пока нет", "Они создадутся из ключевых слов при следующем сборе, или добавьте свои."));
}

function stat(value, label) {
  return h("div", { class: "stat" }, h("b", null, value), h("span", null, label));
}

function queryRow(q, rerender) {
  const stats = q.last_run_at
    ? `нашёл ${q.last_found} · нового ${q.last_new} · всего нового ${q.total_new} за ${q.runs} · ${timeAgo(q.last_run_at)}`
    : "ещё не запускался";
  return h("div", { class: "query" + (q.enabled ? "" : " is-muted") },
    h("span", { class: "badge" }, T.source[q.source] || q.source),
    h("div", { class: "grow" },
      h("div", { class: "query-text" }, q.text, q.origin === "user" && h("span", { class: "badge badge-ai" }, "ваш"),
        q.stale && h("span", { class: "badge badge-warn", title: "Профиль изменился; запрос обновится при следующем успешном сборе" }, "старый профиль")),
      q.basis?.some((b) => b.startsWith("need:")) && h("div", { class: "muted xs" },
        "Потребность: ", q.basis.filter((b) => b.startsWith("need:")).map((b) => b.slice(5)).join(", ")),
      h("div", { class: "faint xs" }, stats, q.resting_days ? ` · отдыхает до ${q.resting_days} дн.` : "")),
    toggle("", q.enabled, async (enabled) => {
      try { await put(`/queries/${q.id}`, { enabled }); rerender(); } catch (error) { toast(errorText(error), { kind: "error" }); }
    }),
    button("", { kind: "ghost", size: "sm", iconName: "trash", title: "Удалить запрос", onClick: async () => {
      try { await del(`/queries/${q.id}`); rerender(); } catch (error) { toast(errorText(error), { kind: "error" }); }
    } }));
}

async function profileTab(body, project, rerender) {
  let updates = [];
  let dependencies = [];
  try {
    updates = (await get("/stack-updates")).filter((u) => u.projects.includes(project.name));
    dependencies = await get(`/stack-watch?project_id=${project.id}`);
  } catch { /* the rest of the profile still renders */ }
  const tags = (values, cls = "tag") => values?.length ? h("div", { class: "tags" }, values.map((v) => h("span", { class: cls }, v))) : h("p", { class: "faint small" }, "Не задано.");
  const remove = button("Удалить проект", { kind: "danger", iconName: "trash" });
  remove.addEventListener("click", async () => {
    const ok = await confirmDialog(`Удалить «${project.name}»?`,
      "Удалятся его поисковые запросы, решения, голоса и брифинги. Сами находки останутся.", { confirmLabel: "Удалить", danger: true });
    if (!ok) return;
    try {
      await del(`/projects/${project.id}`);
      await loadProjects(true);
      toast("Проект удалён", { kind: "success" });
      location.hash = "#/projects";
    } catch (error) {
      toast(errorText(error), { kind: "error" });
    }
  });
  mount(body,
    h("div", { class: "profile" },
      h("section", { class: "dsec" }, h("h3", null, "Ключевые слова"), tags(project.keywords)),
      h("section", { class: "dsec" }, h("h3", null, "Потребности"),
        project.needs?.length ? h("ul", { class: "needs" }, project.needs.map((n) => h("li", null, n))) : h("p", { class: "faint small" }, "Не заданы. Их можно получить импортом из папки проекта.")),
      h("section", { class: "dsec" }, h("h3", null, "Стек"), tags(project.stack, "tag tag-stack"),
        project.stack_path && h("p", { class: "faint xs" }, `Папка: ${project.stack_path}`,
          project.stack_scanned_at ? ` · просканирована ${timeAgo(project.stack_scanned_at)}` : "")),
      updates.length ? h("section", { class: "dsec" }, h("h3", null, "Обновления стека"),
        updates.map((u) => h("div", { class: "change-row" }, icon("package", { size: 16 }),
          h("span", { class: "grow" }, external(u.url, u.name), ` В реестре: ${u.previous || "?"} → `, h("b", null, u.latest)),
          u.major && h("span", { class: "badge badge-warn" }, "major"), h("span", { class: "faint xs" }, timeAgo(u.changed_at))))) : null,
      dependencies.length ? h("section", { class: "dsec" }, h("h3", null, "Зависимости проекта"),
        dependencies.map((d) => h("div", { class: "change-row" }, icon("package", { size: 16 }),
          h("div", { class: "grow" }, h("b", null, d.package_key),
            h("div", { class: "small muted" }, dependencyVersions(d)),
            h("div", { class: "faint xs" }, `${d.ecosystem} · ${d.scope}`, d.manifest_path && ` · ${d.manifest_path}`))))) : null,
      h("div", { class: "form-actions" },
        remove, h("span", { class: "grow" }),
        button("Изменить профиль и импорт", { kind: "primary", iconName: "edit", onClick: () => openProjectModal(project, { onSaved: rerender }) }))));
}
