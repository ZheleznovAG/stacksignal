import { post } from "../api.js";
import { h } from "../dom.js";
import { errorText, timeAgo } from "../i18n.js";
import { icon } from "../icons.js";
import { projectName } from "../store.js";
import { button, toast } from "../ui.js";
import { openItem } from "./item-drawer.js";

// An AI briefing across several items. It persists across runs: same signal again = same opportunity.

export function opportunityCard(o, { onChange, compact = false } = {}) {
  const setState = (state, label, kind = "secondary") => {
    const btn = button(label, { kind, size: "sm" });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await post(`/opportunities/${o.id}/state`, { state });
        toast(state === "done" ? "Отмечено: сделано" : state === "dismissed" ? "Скрыто — больше не всплывёт" : "Снова активно", { kind: "success", timeout: 2500 });
        onChange?.();
      } catch (error) {
        btn.disabled = false;
        toast(errorText(error), { kind: "error" });
      }
    });
    return btn;
  };
  const meta = [
    `впервые ${timeAgo(o.first_seen_at)}`,
    o.times_seen > 1 && `подтверждено в ${o.times_seen} сборах`,
    `${o.item_ids.length} источн.` + (o.evidence_added ? ` (+${o.evidence_added})` : ""),
    o.origin === "heuristic" && "по ключевым словам, не AI",
  ].filter(Boolean).join(" · ");
  return h("article", { class: "opp" + (o.state !== "active" ? ` opp-${o.state}` : "") },
    h("div", { class: "opp-top" },
      o.project_slug && h("span", { class: "badge" }, icon("folder", { size: 12 }), projectName(o.project_slug)),
      o.state === "faded" && h("span", { class: "badge badge-kw" }, "угасает")),
    h("h3", { class: "opp-title" }, o.title),
    h("p", { class: compact ? "opp-body clamp-3" : "opp-body" }, o.body),
    o.evidence?.length > 0 && h("div", { class: "opp-evidence" },
      o.evidence.slice(0, compact ? 4 : 20).map((e) =>
        h("button", { class: "chip-link", type: "button", onClick: () => openItem(e.id) }, e.name))),
    h("div", { class: "opp-foot" },
      h("span", { class: "faint xs" }, meta),
      h("span", { class: "grow" }),
      o.state === "active" || o.state === "faded"
        ? [setState("dismissed", "Скрыть", "ghost"), setState("done", "Сделано", "secondary")]
        : setState("active", "Вернуть")));
}
