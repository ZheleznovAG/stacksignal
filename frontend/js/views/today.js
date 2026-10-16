import { get } from "../api.js";
import { external, h, mount } from "../dom.js";
import { T, count, duration, errorText, fmtNum, fmtToday, pct, timeAgo } from "../i18n.js";
import { icon } from "../icons.js";
import { health } from "../store.js";
import { alertBox, empty, pageHead, sectionHead, segmented, skeletonGrid } from "../ui.js";
import { statusChip } from "../components/badges.js";
import { itemCard } from "../components/item-card.js";
import { openItem } from "../components/item-drawer.js";
import { opportunityCard } from "../components/opportunity.js";
import { dependencyVersions } from "../components/dependency.js";
import { analysisCoverage } from "../components/analysis-coverage.js";
import { quickDecision } from "../components/quick-decision.js";
import { loadNeedSummary } from "../components/needs.js";

// The daily routine on one screen: is everything working, what is new for my projects, what waits for
// a decision, which briefings to read, and what changed in what I already use.

export async function render(root) {
  const day = fmtToday();
  mount(root, pageHead("Сегодня", day[0].toUpperCase() + day.slice(1)), skeletonGrid(6));
  let data, stats;
  try {
    [data, stats] = await Promise.all([get("/today"), get("/stats"), health.ready]);
  } catch (error) {
    mount(root, pageHead("Сегодня"), alertBox("error", "Не удалось загрузить сводку", errorText(error)));
    return;
  }
  const reload = () => { if (root.isConnected) render(root); };
  const needSummary = h("section", { class: "section" });
  mount(root,
    pageHead("Сегодня", day[0].toUpperCase() + day.slice(1)),
    healthBlock(data.health, stats),
    needSummary,
    freshSection(data.fresh, reload),
    h("div", { class: "grid-2 section" }, inboxPanel(data.inbox, reload), briefingsPanel(data.opportunities, reload)),
    changesSection(data.changes));
  loadNeedSummary(needSummary);
}

// ---------- health ----------

function healthBlock(run, stats) {
  const alerts = [];
  const gateway = health.value?.ai_gateway;
  if (gateway && !gateway.ready) {
    alerts.push(alertBox("warn", `AI-шлюз не готов: ${T.gatewayCode[gateway.code] || gateway.code}`,
      "Пока он не работает, новые находки оцениваются только по ключевым словам.",
      h("a", { class: "btn btn-secondary btn-sm", href: "#/settings/collection" }, "Подробнее")));
  }
  const pending = stats.items - stats.ai_analyzed;
  if (run.ai_silent && pending > 0) {
    alerts.push(alertBox("warn", "В последнем сборе AI не прочитал ни одной находки",
      `Сейчас без AI-анализа: ${fmtNum(pending)}. Подробности — в «Настройки → Сбор данных».`));
  }
  const last = run.last_run;
  if (!last) {
    return h("div", { class: "section-tight stack" }, alerts, analysisCoverage(stats),
      empty("radar", "Сборов ещё не было", "Нажмите «Собрать» вверху — первый сбор занимает около 10 минут."));
  }
  const total = (last.stages || []).reduce((sum, s) => sum + (s.duration_s || 0), 0);
  const tone = last.status === "completed" ? "ok" : last.status === "failed" ? "bad" : "warn";
  return h("div", { class: "section-tight stack" }, alerts, analysisCoverage(stats),
    h("a", { class: "runline", href: "#/settings/collection", title: "Подробности сборов" },
      h("span", { class: `runline-status runline-${tone}` }, h("i", { class: "dot" }),
        `Сбор ${T.runStatus[last.status] || last.status}`),
      h("span", { class: "runline-item" }, h("b", null, timeAgo(last.finished_at || last.started_at)), "последний"),
      h("span", { class: "runline-item" }, h("b", { class: "num" }, fmtNum(last.items_found)), "найдено"),
      h("span", { class: "runline-item" }, h("b", { class: "num" }, fmtNum(last.items_new)), "новых"),
      run.ai_read_last_run !== null && run.ai_read_last_run !== undefined &&
        h("span", { class: "runline-item" }, h("b", { class: "num" }, fmtNum(run.ai_read_last_run)), "AI за этот сбор"),
      total > 0 && h("span", { class: "runline-item" }, h("b", null, duration(total)), "длился"),
      h("span", { class: "grow" }),
      icon("chevron", { size: 16 })));
}

// ---------- fresh items ----------

function freshSection(items, reload) {
  const grid = h("div", { class: "grid" });
  const projects = [...new Map(items.map((i) => [i.matched_relevance.project_slug, i.matched_relevance.project_name])).entries()];
  const show = (slug) => mount(grid, items.filter((i) => !slug || i.matched_relevance.project_slug === slug)
    .map((i) => itemCard(i, { quickDecisions: true, onDecision: reload })));
  show("");
  const filter = projects.length > 1 &&
    segmented([["", "Все"], ...projects.map(([slug, name]) => [slug, name])], "", show, { label: "Проект" });
  return h("section", { class: "section" },
    sectionHead("Новое для ваших проектов", { count: items.length, hint: "за неделю, релевантность от 60%, решения ещё нет" }),
    filter && h("div", { class: "toolbar" }, filter),
    items.length ? grid : empty("sparkle", "За неделю ничего сильно релевантного",
      "Новое по-прежнему собирается — загляните в «Обзор» или проверьте ключевые слова проектов.",
      h("a", { class: "btn btn-secondary btn-sm", href: "#/explore" }, "Открыть обзор")));
}

// ---------- inbox and briefings ----------

function inboxPanel(inbox, reload) {
  const total = inbox.reduce((sum, row) => sum + row.count, 0);
  return h("section", { class: "panel" },
    h("div", { class: "panel-head" }, icon("inbox", { size: 18 }), h("h2", { class: "section-title" }, "Ждут решения"),
      h("span", { class: "count" }, String(total))),
    inbox.length
      ? inbox.map((row) => h("div", { class: "inbox-row" },
          h("a", { class: "inbox-project", href: `#/projects/${encodeURIComponent(row.project_slug)}/decisions` },
            h("b", null, row.project_name), h("span", { class: "count" }, row.capped ? `${row.count}+` : String(row.count)),
            icon("chevron", { size: 14 })),
          h("div", { class: "inbox-items" }, row.top.map((t) =>
            h("div", { class: "inbox-candidate", dataset: { itemId: t.id, projectSlug: row.project_slug } },
              h("button", { class: "inbox-item", type: "button", onClick: () => openItem(t.id) },
                h("span", { class: "inbox-item-name" }, t.name), h("span", { class: "rel rel-high" }, pct(t.score))),
              quickDecision(t, row, reload))))))
      : h("div", { class: "panel-empty" }, "Всё разобрано. Новые релевантные находки появятся здесь."));
}

function briefingsPanel(opportunities, reload) {
  return h("section", { class: "panel" },
    h("div", { class: "panel-head" }, icon("bolt", { size: 18 }), h("h2", { class: "section-title" }, "Брифинги"),
      h("span", { class: "count" }, String(opportunities.length)),
      h("a", { class: "panel-link", href: "#/explore/briefings" }, "Все")),
    opportunities.length
      ? h("div", { class: "panel-list" }, opportunities.map((o) => opportunityCard(o, { onChange: reload, compact: true })))
      : h("div", { class: "panel-empty" }, "Активных брифингов нет. AI пишет их после сборов, когда несколько находок складываются в повод пересмотреть решение."));
}

// ---------- what changed ----------

function changesSection({ stack_updates: updates, releases, revisit }) {
  if (!updates.length && !releases.length && !revisit.length) return null;
  const rows = [
    ...revisit.map((e) => {
      const c = e.changes;
      const what = [
        c.release_count && `${c.release_count} ${c.release_count === 1 ? "новый релиз" : "новых релиза"} (${c.releases[0].tag})`,
        ...Object.entries(c.shifts).map(([k, v]) => `${k === "momentum" ? "рост" : k === "maturity" ? "зрелость" : k} ${v.before} → ${v.now}`),
        c.alternatives.length && `альтернативы: ${c.alternatives.map((a) => a.name).join(", ")}`,
      ].filter(Boolean).join(" · ");
      return change("history", h("button", { class: "linklike", type: "button", onClick: () => openItem(e.item_id) }, e.item_name),
        [e.project_name, " · ", statusChip(e.status, { decision: true }), what && ` · ${what}`], "Стоит пересмотреть");
    }),
    ...updates.map((u) => change("package", external(u.url, u.name),
      [u.declared || u.resolved ? dependencyVersions(u) : `В реестре: ${u.previous || "?"} → ${u.latest}`,
        ` · ${u.projects.join(", ")}`], "Обновление стека")),
    ...releases.map((r) => change("tag", h("button", { class: "linklike", type: "button", onClick: () => openItem(r.item_id) }, r.name),
      [external(r.url, r.tag), ` · ${timeAgo(r.published_at)}`], "Релиз")),
  ];
  return h("section", { class: "section" }, sectionHead("Что изменилось в том, что вы используете", { count: rows.length }),
    h("div", { class: "panel" }, rows));
}

function change(iconName, title, detail, kind) {
  return h("div", { class: "change-row" }, h("span", { class: "change-icon" }, icon(iconName, { size: 16 })),
    h("div", { class: "grow" }, h("div", { class: "change-title" }, title), h("div", { class: "muted small" }, detail)),
    h("span", { class: "faint xs nowrap" }, kind));
}
