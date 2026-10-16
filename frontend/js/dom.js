// A tiny hyperscript helper. Text always goes in through textContent, so data from the API can never
// become markup; the only innerHTML is the static icon set.

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "dataset") Object.assign(el.dataset, value);
      else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
      else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === "value" || key === "checked") el[key] = value;
      else if (value === true) el.setAttribute(key, "");
      else el.setAttribute(key, value);
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of [children].flat(Infinity)) {
    if (child === undefined || child === null || child === false || child === "") continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

export function mount(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

export function safeUrl(value) {
  try {
    const url = new URL(value, location.href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "#";
  } catch {
    return "#";
  }
}

export function external(href, children, props = {}) {
  return h("a", { href: safeUrl(href), target: "_blank", rel: "noopener noreferrer", ...props }, children);
}

// Stops a click on an inner control from also triggering the clickable card around it.
export const stop = (handler) => (event) => {
  event.stopPropagation();
  handler(event);
};
