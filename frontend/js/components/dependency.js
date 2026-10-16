import { h } from "../dom.js";

export function dependencyVersions(dep) {
  return h("span", null,
    dep.declared ? `Заявлено: ${dep.declared} · ` : "Версия в проекте не указана · ",
    dep.resolved ? `Зафиксировано: ${dep.resolved} · ` : "",
    `В реестре: ${dep.latest || "ещё не проверено"}`,
    ["major", "minor", "patch"].includes(dep.update_kind)
      ? h("span", { class: "badge badge-warn" }, dep.update_kind) : null);
}
