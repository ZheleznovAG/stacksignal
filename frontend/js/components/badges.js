import { h } from "../dom.js";
import { T, fmtNum, pct, timeAgo, isFresh } from "../i18n.js";
import { icon, SOURCE_ICON } from "../icons.js";

export function sourceBadge(source) {
  return h("span", { class: "source" }, icon(SOURCE_ICON[source] || "cube", { size: 14 }), T.source[source] || source);
}

export function aiBadge(analyzed) {
  return analyzed
    ? h("span", { class: "badge badge-ai", title: T.aiHint }, icon("sparkle", { size: 12 }), T.ai)
    : h("span", { class: "badge badge-kw", title: T.keywordsHint }, T.keywords);
}

export const freshBadge = (iso) => (isFresh(iso) ? h("span", { class: "badge badge-new" }, T.fresh) : null);

export function statusChip(status, { decision = false } = {}) {
  const label = (decision ? T.decision[status] : T.status[status]) || status;
  return h("span", { class: `status status-${status}`, title: (decision ? T.decisionHint : T.statusHint)[status] || "" },
    h("i", { class: "dot" }), label);
}

export function relevanceChip(name, score) {
  const level = score >= 0.8 ? "high" : score >= 0.6 ? "mid" : "low";
  return h("span", { class: `rel rel-${level}` }, h("span", { class: "rel-name" }, name), h("strong", null, pct(score)));
}

export function meter(label, value, hint, kind = "momentum") {
  return h("span", { class: `meter meter-${kind}`, title: `${hint}: ${pct(value)}` },
    h("span", { class: "meter-label" }, label),
    h("span", { class: "meter-bar" }, h("i", { style: { width: pct(value) } })));
}

export function bars(item) {
  return [meter(T.momentum, item.momentum_score, T.momentumHint), meter(T.maturity, item.maturity_score, T.maturityHint, "maturity")];
}

// The one or two numbers that say most about an item, per source.
export function keyMetrics(item) {
  const out = [];
  if (item.source === "github") {
    out.push(h("span", { class: "metric", title: "Звёзды" }, icon("star", { size: 13 }), fmtNum(item.stars)));
    if (item.forks) out.push(h("span", { class: "metric", title: "Форки" }, icon("fork", { size: 13 }), fmtNum(item.forks)));
  } else if (item.source === "huggingface") {
    out.push(h("span", { class: "metric", title: "Загрузки" }, icon("download", { size: 13 }), fmtNum(item.downloads)));
    out.push(h("span", { class: "metric", title: "Лайки" }, icon("heart", { size: 13 }), fmtNum(item.likes)));
  } else if (item.source === "npm" || item.source === "pypi") {
    if (item.downloads) out.push(h("span", { class: "metric", title: "Загрузок в месяц" }, icon("download", { size: 13 }), `${fmtNum(item.downloads)}/мес`));
  } else if (item.source === "arxiv" && item.owner) {
    out.push(h("span", { class: "metric metric-text", title: item.owner }, icon("paper", { size: 13 }), item.owner.split(",")[0]));
  }
  return out;
}

export function seen(item) {
  return h("span", { class: "metric", title: "Впервые замечено" }, icon("clock", { size: 13 }), timeAgo(item.first_seen_at));
}
