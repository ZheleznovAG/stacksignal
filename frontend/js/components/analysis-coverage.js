import { h } from "../dom.js";
import { fmtNum, T, timeAgo } from "../i18n.js";

export function analysisCoverage(stats) {
  const pending = stats.items - stats.ai_analyzed;
  return h("div", { class: "panel panel-pad analysis-coverage" },
    h("div", { class: "section-title" }, `AI прочитал ${fmtNum(stats.ai_analyzed)} из ${fmtNum(stats.items)} находок`),
    h("div", { class: "muted small" }, stats.items === 0 ? "Пока нет находок для анализа."
      : pending > 0 ? `Ждут AI-анализа: ${fmtNum(pending)}.` : "Все текущие находки прочитаны AI."),
    h("div", { class: "faint small" }, "Включая анализ вне сборов. Архив не учитывается."),
    stats.last_ai_attempt?.error_code && h("div", { class: "muted small" },
      `Последняя попытка AI: ${T.gatewayCode[stats.last_ai_attempt.error_code] || stats.last_ai_attempt.error_code} · ${timeAgo(stats.last_ai_attempt.at)}`));
}
