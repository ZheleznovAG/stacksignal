import { h } from "../dom.js";
import { count, pct } from "../i18n.js";
import { button } from "../ui.js";
import { relevanceChip } from "./badges.js";
import { openItem } from "./item-drawer.js";

export function capabilityCard(cap) {
  const items = h("div", { class: "cap-items" });
  let expanded = false;
  const renderItems = () => items.replaceChildren(...cap.items.slice(0, expanded ? cap.items.length : 4).map((item) =>
    h("button", { class: "cap-item", type: "button", onClick: () => openItem(item.id) },
      h("span", { class: "cap-item-name" }, item.name),
      h("span", { class: "faint xs num" }, `рел. ${pct(item.relevance)} · рост ${pct(item.momentum)}`))));
  renderItems();
  const more = cap.items.length > 4 ? button(`Все ${cap.items.length}`, { kind: "ghost", size: "sm" }) : null;
  more?.addEventListener("click", () => {
    expanded = !expanded;
    renderItems();
    more.textContent = expanded ? "Свернуть" : `Все ${cap.items.length}`;
  });
  const ask = button("Спросить чат", { kind: "ghost", size: "sm", iconName: "chat" });
  ask.addEventListener("click", () => {
    sessionStorage.setItem("chat-draft", `Что сейчас лучше использовать для «${cap.name}»?`);
    location.hash = "#/chat";
  });
  return h("article", { class: "cap" },
    h("div", { class: "cap-head" }, h("h3", { class: "cap-title" }, cap.name),
      h("span", { class: "count" }, count(cap.item_count, "элемент", "элемента", "элементов"))),
    cap.projects.length ? h("div", { class: "tags" }, cap.projects.map((p) => relevanceChip(p.name, p.score))) : null,
    items,
    h("div", { class: "cap-foot" }, more, h("span", { class: "grow" }), ask));
}
