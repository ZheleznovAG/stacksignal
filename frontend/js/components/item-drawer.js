import { del, get, post, put } from "../api.js";
import { external, h, mount } from "../dom.js";
import { T, errorText, fmtDateTime, fmtNum, pct, timeAgo } from "../i18n.js";
import { icon } from "../icons.js";
import { emit, getProjects, loadProjects } from "../store.js";
import { busy, button, field, input, openDrawer, select, textarea, toast } from "../ui.js";
import { aiBadge, freshBadge, sourceBadge, statusChip } from "./badges.js";

// Everything about one item: status, why it matters for each project (with votes), your decisions per
// project, notes and history. Opening another item reuses the open drawer.

let current = null; // {panel, close}

export async function openItem(id) {
  if (!current || !current.panel.isConnected) {
    openDrawer((panel, close) => (current = { panel, close }));
  }
  const { panel } = current;
  mount(panel, h("div", { class: "drawer-loading" }, h("div", { class: "skeleton", style: { height: "28px", width: "60%" } }),
    h("div", { class: "skeleton", style: { height: "90px" } }), h("div", { class: "skeleton", style: { height: "160px" } })));
  try {
    const [item] = await Promise.all([get(`/items/${id}`), loadProjects()]);
    render(panel, item);
  } catch (error) {
    mount(panel, drawerHead(null), h("div", { class: "drawer-body" }, h("p", { class: "muted" }, errorText(error))));
  }
}

function render(panel, item) {
  const body = panel.querySelector(".drawer-body");
  const scroll = body ? body.scrollTop : 0;
  const update = (updated) => {
    emit("item-updated", updated);
    render(panel, updated);
  };
  mount(panel, drawerHead(item),
    h("div", { class: "drawer-body" },
      statusSection(item, update),
      summarySection(item),
      numbersSection(item),
      linksSection(item),
      relevanceSection(item, update),
      decisionsSection(item, update),
      notesSection(item, update),
      historySection(item)));
  panel.querySelector(".drawer-body").scrollTop = scroll;
}

function drawerHead(item) {
  const close = h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Закрыть", onClick: () => current?.close() }, icon("x"));
  if (!item) return h("div", { class: "drawer-head" }, h("div", { class: "grow" }), close);
  return h("div", { class: "drawer-head" },
    h("div", { class: "grow" },
      h("div", { class: "drawer-kicker" }, sourceBadge(item.source), aiBadge(item.ai_analyzed), freshBadge(item.first_seen_at)),
      h("h2", { class: "drawer-title" }, item.name),
      item.owner && item.source !== "arxiv" && h("div", { class: "drawer-owner" }, item.owner)),
    external(item.url, [icon("external", { size: 15 }), "Открыть"], { class: "btn btn-secondary btn-sm" }),
    close);
}

function section(title, ...children) {
  return h("section", { class: "dsec" }, h("h3", null, title), ...children);
}

// ---------- status ----------

function statusSection(item, update) {
  const options = Object.entries(T.status).map(([key, label]) => [key, label]);
  const control = select(options, item.status, {
    "aria-label": "Статус",
    onChange: async () => {
      try {
        update(await post(`/items/${item.id}/status`, { status: control.value, note: "Изменено в интерфейсе" }));
        toast(`Статус: ${T.status[control.value]}`, { kind: "success", timeout: 2000 });
      } catch (error) {
        toast(errorText(error), { kind: "error" });
      }
    },
  });
  return h("div", { class: "status-row" },
    h("span", { class: "field-label" }, "Общий статус"), control,
    h("span", { class: "faint xs" }, item.status_source === "you" ? "задан вами" : "предложен системой"));
}

// ---------- what it is ----------

function summarySection(item) {
  return h("section", { class: "dsec" },
    h("p", { class: "lead" }, item.llm_summary || item.description || "Без описания."),
    item.why_interesting && h("div", { class: "callout" }, icon("sparkle", { size: 16 }),
      h("div", null, h("b", null, "Почему заметно. "), item.why_interesting)),
    item.llm_summary && item.description && item.description !== item.llm_summary &&
      h("details", { class: "fold" }, h("summary", null, "Исходное описание"), h("p", { class: "muted small" }, item.description)));
}

function stat(label, value, hint) {
  return h("div", { class: "stat", title: hint || "" }, h("b", null, value), h("span", null, label));
}

function numbersSection(item) {
  const stats = [];
  if (item.source === "github") stats.push(stat("звёзд", fmtNum(item.stars)), stat("форков", fmtNum(item.forks)));
  if (item.source === "huggingface") stats.push(stat("загрузок", fmtNum(item.downloads)), stat("лайков", fmtNum(item.likes)));
  if (item.source === "npm" || item.source === "pypi") stats.push(stat("загрузок/мес", fmtNum(item.downloads)));
  stats.push(stat(T.momentum, pct(item.momentum_score), T.momentumHint), stat(T.maturity, pct(item.maturity_score), T.maturityHint));
  const facts = [
    ["Впервые замечено", timeAgo(item.first_seen_at)],
    item.source_updated_at && ["Обновлено у источника", timeAgo(item.source_updated_at)],
  ].filter(Boolean);
  const release = item.latest_release;
  return section("Цифры",
    h("div", { class: "stats" }, stats),
    h("dl", { class: "facts" }, facts.map(([k, v]) => [h("dt", null, k), h("dd", null, v)]),
      release && [h("dt", null, "Последний релиз"),
        h("dd", null, external(release.url, `${release.tag}`), ` · ${timeAgo(release.published_at)}`, release.prerelease ? " · пре-релиз" : "")]),
    item.capabilities?.length > 0 && h("div", { class: "tags" }, item.capabilities.map((c) => h("span", { class: "tag" }, c.name))));
}

function linksSection(item) {
  const rows = [];
  if (item.same_as) rows.push(linkRow("Та же технология", item.same_as));
  (item.canonical_of || []).forEach((p) => rows.push(linkRow("Тот же проект как пакет", p)));
  (item.found_via || []).forEach((f) => rows.push(!f.project_slug
    ? h("div", { class: "link-row" }, icon("star", { size: 14 }), h("span", null, "Звезда на GitHub"))
    : f.project_slug === "@author"
    ? h("div", { class: "link-row" }, icon("star", { size: 14 }), h("span", null, "Репозиторий автора ", h("b", null, f.query), ", за которым вы наблюдаете"))
    : h("div", { class: "link-row" }, icon("search", { size: 14 }),
      h("span", null, "Найдено поиском для ", h("b", null, getProjects().find((p) => p.slug === f.project_slug)?.name || f.project_slug),
        " по запросу «", f.query, "»"))));
  return rows.length ? section("Связи", rows) : null;
}

function linkRow(label, other) {
  return h("div", { class: "link-row" }, icon("link", { size: 14 }),
    h("span", null, `${label}: `, h("button", { class: "linklike", type: "button", onClick: () => openItem(other.id) },
      `${other.name} (${T.source[other.source] || other.source})`)));
}

// ---------- relevance and votes ----------

function relevanceSection(item, update) {
  const votes = item.feedback || {};
  const rows = (item.relevance || [])
    .filter((r) => r.score > 0.15 || votes[r.project_slug])
    .sort((a, b) => b.score - a.score);
  if (!rows.length) return section("Кому это полезно", h("p", { class: "muted small" }, "Ни к одному проекту не подходит заметно."));
  return section("Кому это полезно",
    h("div", { class: "rel-list" }, rows.map((r) => relevanceRow(item, r, votes[r.project_slug] || 0, update))),
    h("p", { class: "faint xs" }, "👍/👎 сильнее оценки AI и переживают повторный анализ. Нажмите ещё раз, чтобы снять."));
}

function relevanceRow(item, r, vote, update) {
  const send = (verdict) => async () => {
    try {
      update(await post(`/items/${item.id}/feedback`, { project_slug: r.project_slug, verdict: vote === verdict ? 0 : verdict }));
    } catch (error) {
      toast(errorText(error), { kind: "error" });
    }
  };
  const voteBtn = (verdict, name, title) => h("button", {
    type: "button", class: "vote" + (vote === verdict ? " is-on" : "") + (verdict < 0 ? " vote-down" : ""),
    "aria-pressed": String(vote === verdict), title, "aria-label": `${title}: ${r.project_name}`, onClick: send(verdict),
  }, icon(name, { size: 15 }));
  return h("div", { class: "rel-row" + (vote === -1 ? " is-muted" : "") },
    h("div", { class: "rel-row-head" },
      h("span", { class: "rel-row-name" }, r.project_name),
      h("span", { class: "bar" }, h("i", { style: { width: pct(r.score) } })),
      h("strong", { class: "num" }, pct(r.score)),
      h("span", { class: "votes" }, voteBtn(1, "up", "Полезно"), voteBtn(-1, "down", "Не подходит"))),
    r.rationale && h("p", { class: "rel-row-reason" }, r.rationale));
}

// ---------- decisions per project ----------

function decisionsSection(item, update) {
  const list = h("div", { class: "decisions" });
  const addBtn = button("Решение для проекта", { iconName: "plus", size: "sm", onClick: () => addBtn.replaceWith(decisionForm(item, null, update, () => render(current.panel, item))) });
  (item.decisions || []).forEach((d) => list.append(decisionRow(item, d, update)));
  return section("Решения по проектам",
    h("p", { class: "faint xs" }, "Одно и то же можно пробовать в одном проекте и отклонить в другом. Цель, критерий и вердикт помогут через месяц вспомнить «почему»."),
    list, addBtn);
}

function decisionRow(item, d, update) {
  const row = h("div", { class: "decision" },
    h("div", { class: "decision-head" },
      h("b", null, d.project_name), statusChip(d.status, { decision: true }),
      h("span", { class: "faint xs" }, timeAgo(d.decided_at)),
      h("span", { class: "grow" }),
      button("", { kind: "ghost", size: "sm", iconName: "edit", title: "Изменить решение",
        onClick: () => row.replaceWith(decisionForm(item, d, update, () => row.isConnected || render(current.panel, item))) }),
      button("", { kind: "ghost", size: "sm", iconName: "trash", title: "Удалить решение",
        onClick: async () => {
          try { update(await del(`/items/${item.id}/decisions/${d.project_slug}`)); } catch (error) { toast(errorText(error), { kind: "error" }); }
        } })),
    [["Цель", d.goal], ["Критерий успеха", d.criteria], ["Вердикт", d.verdict], ["Вернуться, когда", d.revisit_when]]
      .filter(([, v]) => v).map(([k, v]) => h("div", { class: "decision-line" }, h("span", null, k), v)));
  return row;
}

function decisionForm(item, existing, update, cancel) {
  const projects = getProjects();
  const preferred = existing?.project_slug || item.matched_relevance?.project_slug
    || (item.relevance || []).slice().sort((a, b) => b.score - a.score)[0]?.project_slug || projects[0]?.slug;
  const project = select(projects.map((p) => [p.slug, p.name]), preferred, { disabled: Boolean(existing) });
  const status = select(Object.entries(T.decision), existing?.status || "assess");
  const goal = input({ value: existing?.goal || "", placeholder: "что хотите попробовать или проверить" });
  const criteria = input({ value: existing?.criteria || "", placeholder: "например: 20 fps на среднем телефоне" });
  const verdict = input({ value: existing?.verdict || "", placeholder: "что вышло, или почему нет" });
  const revisit = input({ value: existing?.revisit_when || "", placeholder: "например: появится сборка под GPU" });
  const save = button("Сохранить", { kind: "primary", size: "sm" });
  save.addEventListener("click", () => busy(save, async () => {
    try {
      update(await put(`/items/${item.id}/decisions/${project.value}`, {
        status: status.value, goal: goal.value, criteria: criteria.value, verdict: verdict.value, revisit_when: revisit.value,
      }));
      toast("Решение сохранено", { kind: "success", timeout: 2000 });
    } catch (error) {
      toast(errorText(error), { kind: "error" });
    }
  }));
  return h("div", { class: "decision-form" },
    h("div", { class: "form-row" }, field("Проект", project), field("Решение", status)),
    field("Цель", goal), field("Критерий успеха", criteria), field("Вердикт", verdict), field("Вернуться, когда", revisit),
    h("div", { class: "form-actions" }, button("Отмена", { kind: "ghost", size: "sm", onClick: cancel }), save));
}

// ---------- notes and history ----------

function notesSection(item, update) {
  const notes = item.notes || [];
  const text = textarea({ rows: 2, placeholder: "Почему решили так, что проверить потом… Чат найдёт это позже." });
  const save = button("Добавить заметку", { size: "sm", iconName: "note" });
  save.addEventListener("click", () => {
    if (!text.value.trim()) return text.focus();
    return busy(save, async () => {
      try { update(await post(`/items/${item.id}/feedback`, { note: text.value.trim() })); } catch (error) { toast(errorText(error), { kind: "error" }); }
    });
  });
  return section(`Заметки${notes.length ? ` · ${notes.length}` : ""}`,
    notes.length ? h("div", { class: "notes" }, notes.slice().reverse().map((n) =>
      h("div", { class: "note" }, h("div", { class: "faint xs" }, fmtDateTime(n.created_at)), h("div", null, n.note)))) : null,
    text, h("div", { class: "form-actions" }, save));
}

function historySection(item) {
  const history = item.status_history || [];
  if (!history.length) return null;
  const projectName = (slug) => getProjects().find((p) => p.slug === slug)?.name;
  return h("details", { class: "fold dsec" }, h("summary", null, `История статусов · ${history.length}`),
    h("ol", { class: "timeline" }, history.slice().reverse().map((entry) =>
      h("li", null, statusChip(entry.status, { decision: Boolean(entry.project_slug) }),
        entry.project_slug && h("b", null, projectName(entry.project_slug) || entry.project_slug),
        h("span", { class: "faint xs" }, fmtDateTime(entry.created_at)),
        entry.note && h("div", { class: "muted small" }, entry.note)))));
}
