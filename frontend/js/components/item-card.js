import { post } from "../api.js";
import { h } from "../dom.js";
import { errorText } from "../i18n.js";
import { icon } from "../icons.js";
import { emit, on } from "../store.js";
import { toast } from "../ui.js";
import { aiBadge, bars, freshBadge, keyMetrics, relevanceChip, sourceBadge, statusChip } from "./badges.js";
import { openItem } from "./item-drawer.js";
import { quickDecision } from "./quick-decision.js";

// A compact, scannable card. Everything else (all projects' reasons, decisions, notes, history) lives in
// the detail drawer, so a screen shows twelve items instead of four.
//
// `context` is the project the card is shown for ({project_slug, project_name, score, rationale}); it
// adds that project's reason and one-click 👍/👎. Without it the card shows the item's best projects.

export function itemCard(item, { context, quickDecisions = false, onDecision, onVote } = {}) {
  context = context || item.matched_relevance;
  const dismissed = context && (item.feedback || {})[context.project_slug] === -1;
  const card = h("article", {
    class: "card" + (dismissed ? " is-muted" : ""), tabindex: "0", dataset: { itemId: item.id },
    onClick: () => openItem(item.id),
    onKeydown: (event) => {
      if (event.key === "Enter" && event.target === card) openItem(item.id);
    },
  },
  h("div", { class: "card-top" }, sourceBadge(item.source), aiBadge(item.ai_analyzed), freshBadge(item.first_seen_at),
    item.status !== "new" && statusChip(item.status)),
  h("h3", { class: "card-title" }, item.name),
  h("p", { class: "card-summary" }, item.llm_summary || item.description || "Без описания."),
  relevanceBlock(item, context),
  h("div", { class: "card-foot" },
    h("span", { class: "card-metrics" }, keyMetrics(item)),
    h("span", { class: "card-bars" }, bars(item)),
    context && votes(item, context, onVote)),
  quickDecisions && context && quickDecision(item, context, onDecision));

  card._rerender = (updated) => {
    const next = context ? contextFrom(updated, context) : null;
    card.replaceWith(itemCard(updated, { context: next, quickDecisions, onDecision, onVote }));
  };
  return card;
}

function relevanceBlock(item, context) {
  if (context) {
    return h("div", { class: "card-rel" }, relevanceChip(context.project_name, context.score),
      context.analysis?.scope === "project_relevance" && h("span", {
        class: "badge badge-ai", title: "AI оценил применимость к этому проекту. Отметка анализа самой сводки показана отдельно.",
      }, context.analysis.provider === "claude-code" ? "Релевантность: Claude" : "Релевантность: AI"),
      context.rationale && h("p", { class: "card-reason" }, context.rationale));
  }
  const top = (item.relevance || []).filter((r) => r.score >= 0.3).sort((a, b) => b.score - a.score).slice(0, 3);
  return top.length ? h("div", { class: "card-rel" }, top.map((r) => relevanceChip(r.project_name, r.score))) : null;
}

function contextFrom(item, context) {
  const row = (item.relevance || []).find((r) => r.project_slug === context.project_slug);
  return row ? { project_slug: row.project_slug, project_name: row.project_name, score: row.score, rationale: row.rationale,
    analysis: context.analysis } : context;
}

function votes(item, context, onVote) {
  const current = (item.feedback || {})[context.project_slug] || 0;
  const vote = (verdict) => async (event) => {
    event.stopPropagation();
    const next = current === verdict ? 0 : verdict;
    try {
      const updated = await post(`/items/${item.id}/feedback`, { project_slug: context.project_slug, verdict: next });
      emit("item-updated", updated);
      onVote?.(updated, next);
      if (next) toast(next > 0 ? `Отмечено как полезное для «${context.project_name}»` : `Больше не предлагать для «${context.project_name}»`, { kind: "success", timeout: 2500 });
    } catch (error) {
      toast(errorText(error), { kind: "error" });
    }
  };
  const btn = (verdict, name, title) =>
    h("button", {
      type: "button", class: "vote" + (current === verdict ? " is-on" : "") + (verdict < 0 ? " vote-down" : ""),
      title: current === verdict ? "Снять оценку" : title, "aria-label": title, "aria-pressed": String(current === verdict),
      onClick: vote(verdict),
    }, icon(name, { size: 15 }));
  return h("span", { class: "votes" }, btn(1, "up", "Полезно для проекта"), btn(-1, "down", "Не подходит для проекта"));
}

on("item-updated", (updated) => {
  document.querySelectorAll(`.card[data-item-id="${updated.id}"]`).forEach((card) => card._rerender?.(updated));
});
