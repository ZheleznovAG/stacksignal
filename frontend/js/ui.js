import { h, mount } from "./dom.js";
import { icon } from "./icons.js";

// ---------- buttons, controls ----------

export function button(label, { kind = "secondary", size, iconName, onClick, title, type = "button", disabled } = {}) {
  const classes = ["btn", `btn-${kind}`, size && `btn-${size}`, !label && iconName && "btn-icon"].filter(Boolean).join(" ");
  return h("button", { class: classes, type, title, "aria-label": !label ? title : undefined, onClick, disabled },
    iconName && icon(iconName, { size: size === "sm" ? 14 : 16 }), label);
}

// Shows a spinner on the button while `work` runs, and restores it afterwards.
export async function busy(btn, work, busyLabel) {
  const before = [...btn.childNodes];
  btn.disabled = true;
  btn.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), busyLabel || "");
  try {
    return await work();
  } finally {
    btn.disabled = false;
    btn.replaceChildren(...before);
  }
}

export function segmented(options, value, onChange, { label } = {}) {
  const group = h("div", { class: "segmented", role: "group", "aria-label": label });
  const render = (current) =>
    mount(group, options.map(([key, text, iconName]) =>
      h("button", {
        type: "button", "aria-pressed": String(key === current),
        onClick: () => { render(key); onChange(key); },
      }, iconName && icon(iconName, { size: 14 }), text)));
  render(value);
  return group;
}

export function toggle(label, checked, onChange) {
  const input = h("input", { type: "checkbox", role: "switch", checked, onChange: () => onChange(input.checked) });
  return h("label", { class: "switch" }, input, label);
}

export function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", { class: "field-label" }, label), control,
    hint && h("span", { class: "field-hint" }, hint));
}

export const input = (props = {}) => h("input", { class: "input", ...props });
export const textarea = (props = {}) => h("textarea", { class: "textarea", ...props });

export function select(options, value, props = {}) {
  return h("select", { class: "select", ...props },
    options.map(([key, text]) => h("option", { value: key, selected: key === value }, text)));
}

// ---------- layout pieces ----------

export function pageHead(title, sub, actions) {
  return h("header", { class: "page-head" },
    h("div", null, h("h1", { class: "page-title" }, title), sub && h("p", { class: "page-sub" }, sub)),
    actions && h("div", { class: "page-actions" }, actions));
}

export function sectionHead(title, { count, actions, hint } = {}) {
  return h("div", { class: "section-head" },
    h("h2", { class: "section-title" }, title),
    count !== undefined && h("span", { class: "count" }, String(count)),
    hint && h("span", { class: "section-hint" }, hint),
    actions && h("div", { class: "section-actions" }, actions));
}

export function subTabs(items, current) {
  return h("nav", { class: "tabs", "aria-label": "Разделы" },
    items.map(([key, text, href, iconName]) =>
      h("a", { href, "aria-current": key === current ? "page" : undefined }, iconName && icon(iconName, { size: 15 }), text)));
}

export function empty(iconName, title, text, action) {
  return h("div", { class: "empty" }, icon(iconName, { size: 28 }), h("div", { class: "empty-title" }, title),
    text && h("p", null, text), action);
}

export function skeletonGrid(count = 6, className = "skeleton-card") {
  return h("div", { class: "grid" }, Array.from({ length: count }, () => h("div", { class: `skeleton ${className}` })));
}

export function alertBox(kind, title, text, action) {
  return h("div", { class: `alert alert-${kind}`, role: kind === "warn" || kind === "error" ? "alert" : "status" },
    icon(kind === "info" ? "info" : "alert", { size: 18 }),
    h("div", { class: "alert-body" }, h("div", { class: "alert-title" }, title), text && h("div", null, text)),
    action && h("div", { class: "alert-action" }, action));
}

// ---------- toasts ----------

const toastRoot = () => document.getElementById("toasts");

export function toast(message, { kind = "info", timeout = 4500 } = {}) {
  const iconName = kind === "success" ? "check" : kind === "error" ? "alert" : "info";
  const el = h("div", { class: `toast toast-${kind}`, role: kind === "error" ? "alert" : "status" },
    icon(iconName, { size: 16 }), h("div", null, message),
    h("button", { class: "toast-close", type: "button", "aria-label": "Закрыть", onClick: () => el.remove() }, icon("x", { size: 14 })));
  toastRoot().append(el);
  if (timeout) setTimeout(() => el.remove(), timeout);
  return el;
}

// ---------- overlays: one modal and one drawer at a time ----------

const escapeStack = [];
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && escapeStack.length) {
    event.preventDefault();
    escapeStack[escapeStack.length - 1]();
  }
});

let modalClose = null;

export function closeModal() {
  if (modalClose) modalClose();
}

export function openModal({ title, body, actions, wide = false, onClose }) {
  closeModal();
  const root = document.getElementById("modal-root");
  const previous = document.activeElement;
  const close = () => {
    if (modalClose !== close) return;
    modalClose = null;
    const index = escapeStack.indexOf(close);
    if (index >= 0) escapeStack.splice(index, 1);
    root.replaceChildren();
    onClose?.();
    previous?.focus?.();
  };
  modalClose = close;
  const dialog = h("div", { class: "modal" + (wide ? " modal-wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
    h("div", { class: "modal-head" },
      h("h2", { class: "modal-title" }, title),
      h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Закрыть", onClick: close }, icon("x"))),
    h("div", { class: "modal-body" }, body),
    actions && h("div", { class: "modal-actions" }, typeof actions === "function" ? actions(close) : actions));
  const backdrop = h("div", { class: "modal-backdrop", onMousedown: (e) => e.target === backdrop && close() }, dialog);
  root.replaceChildren(backdrop);
  escapeStack.push(close);
  (dialog.querySelector("input, textarea, select") || dialog.querySelector("button"))?.focus();
  return close;
}

export function confirmDialog(title, text, { confirmLabel = "Подтвердить", danger = false } = {}) {
  return new Promise((resolve) => {
    let answered = false;
    const close = openModal({
      title,
      body: h("p", { class: "muted" }, text),
      actions: (closeModal) => [
        button("Отмена", { kind: "ghost", onClick: closeModal }),
        button(confirmLabel, { kind: danger ? "danger-solid" : "primary", onClick: () => { answered = true; resolve(true); closeModal(); } }),
      ],
      onClose: () => !answered && resolve(false),
    });
    return close;
  });
}

let drawerClose = null;

export function openDrawer(render) {
  closeDrawer();
  const previous = document.activeElement;
  const panel = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": "Подробности" });
  const backdrop = h("div", { class: "drawer-backdrop", onClick: () => close() });
  const close = () => {
    if (drawerClose !== close) return;
    drawerClose = null;
    const index = escapeStack.indexOf(close);
    if (index >= 0) escapeStack.splice(index, 1);
    panel.classList.remove("open");
    backdrop.classList.remove("open");
    setTimeout(() => { panel.remove(); backdrop.remove(); }, 250);
    document.body.classList.remove("no-scroll");
    previous?.focus?.();
  };
  drawerClose = close;
  escapeStack.push(close);
  document.body.append(backdrop, panel);
  document.body.classList.add("no-scroll");
  requestAnimationFrame(() => { panel.classList.add("open"); backdrop.classList.add("open"); });
  render(panel, close);
  return close;
}

export function closeDrawer() {
  if (drawerClose) drawerClose();
}
