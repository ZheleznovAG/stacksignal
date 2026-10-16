import { del, get, put } from "../api.js";
import { h } from "../dom.js";
import { errorText } from "../i18n.js";
import { emit } from "../store.js";
import { busy, button, toast } from "../ui.js";
import { openItem } from "./item-drawer.js";

const ACTIONS = [["assess", "Изучить"], ["trial", "Пробую"], ["adopt", "Использую"],
  ["ignore", "Не подходит"], ["hold", "Позже"]];

// Always scoped to the project visibly named next to the controls. These actions never set
// an item's global status. The existing drawer remains the place for reasons and criteria.
export function quickDecision(item, context, onChange) {
  const path = `/items/${item.id}/decisions/${encodeURIComponent(context.project_slug)}`;
  const controls = h("div", { class: "quick-decision", role: "group",
    "aria-label": `Решение для ${context.project_name}`, onClick: (event) => event.stopPropagation() });
  const changed = (updated) => {
    emit("item-updated", updated);
    onChange?.(updated);
  };
  let saving = false;
  const save = async (status, btn) => {
    if (saving) return;
    saving = true;
    controls.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      await busy(btn, async () => {
        const before = await get(`/items/${item.id}`);
        const previous = before.decisions.find((d) => d.project_slug === context.project_slug);
        const updated = await put(path, { status });
        const saved = updated.decisions.find((d) => d.project_slug === context.project_slug);
        changed(updated);
        const undo = button("Отменить", { kind: "ghost", size: "sm" });
        const notice = toast(h("div", null, `Решение для «${context.project_name}» сохранено.`,
          h("div", { class: "form-actions" }, undo,
            button("Добавить причину", { kind: "ghost", size: "sm", onClick: () => openItem(item.id) }))),
          { kind: "success", timeout: 15000 });
        undo.addEventListener("click", () => busy(undo, async () => {
          try {
            const latest = await get(`/items/${item.id}`);
            const current = latest.decisions.find((d) => d.project_slug === context.project_slug);
            if (current?.updated_at !== saved.updated_at) {
              notice.remove();
              toast("Решение уже изменено. Откройте карточку, чтобы проверить его.");
              return;
            }
            const restored = previous
              ? await put(path, Object.fromEntries(["status", "goal", "criteria", "verdict", "revisit_when"].map((k) => [k, previous[k]])))
              : await del(path);
            notice.remove();
            changed(restored);
          } catch (error) { toast(errorText(error), { kind: "error" }); }
        }));
      });
    } catch (error) {
      toast(errorText(error), { kind: "error" });
    } finally {
      saving = false;
      controls.querySelectorAll("button").forEach((b) => (b.disabled = false));
    }
  };
  controls.append(h("span", { class: "faint xs" }, `Для «${context.project_name}»`),
    h("div", { class: "quick-decision-actions" }, ACTIONS.map(([status, label]) => {
      const btn = button(label, { kind: "secondary", size: "sm", onClick: () => save(status, btn) });
      return btn;
    })));
  return controls;
}
