import { post, put } from "../api.js";
import { h } from "../dom.js";
import { T, errorText } from "../i18n.js";
import { loadProjects } from "../store.js";
import { busy, button, field, input, openModal, select, textarea, toast, toggle } from "../ui.js";

const list = (text, sep = ",") => text.split(sep).map((s) => s.trim()).filter(Boolean);

export function openProjectModal(project, { onSaved } = {}) {
  const name = input({ value: project?.name || "", placeholder: "например, Мой проект" });
  const description = textarea({ rows: 3, value: project?.description || "", placeholder: "Что это за проект и что для него важно" });
  const keywords = textarea({ rows: 2, value: (project?.keywords || []).join(", "), placeholder: "local inference, ocr, speech to text" });
  const needs = textarea({ rows: 3, value: (project?.needs || []).join("\n"), placeholder: "Чего не хватает или что планируется — по строке на пункт" });
  const kind = select(Object.entries(T.contextKind), project?.kind || "project", { "aria-label": "Тип контекста" });
  const lifecycle = select(Object.entries(T.lifecycle), project?.lifecycle_status || "active", { "aria-label": "Состояние" });
  const lifecycleHint = h("span", { class: "field-hint" }, T.lifecycleHint[lifecycle.value]);
  let discovery = project?.discovery_enabled ?? true;
  const discoveryControl = toggle("Искать новые материалы", discovery, (value) => (discovery = value));
  const refreshPolicy = () => {
    lifecycleHint.textContent = T.lifecycleHint[lifecycle.value];
    discoveryControl.querySelector("input").disabled = lifecycle.value === "archived";
  };
  lifecycle.addEventListener("change", refreshPolicy);
  refreshPolicy();

  const body = h("div", { class: "form" },
    field("Название", name),
    field("Описание", description),
    field("Ключевые слова", keywords, "Через запятую. Конкретные («speech to text»), а не общие («android») — от них зависит релевантность."),
    field("Ручные потребности (необязательно)", needs, "Обычно их определит StackSignal из описания проекта. Здесь можно явно добавить то, что он не должен упустить."),
    h("div", { class: "form-row" }, field("Тип контекста", kind), field("Состояние", lifecycle)),
    lifecycleHint, discoveryControl,
    h("p", { class: "field-hint" }, "Выключение поиска сохраняет найденное, решения и наблюдение за используемыми зависимостями."),
    project && importSection(project, { description, keywords, needs }));

  openModal({
    title: project ? `Проект «${project.name}»` : "Новый проект",
    body,
    wide: Boolean(project),
    actions: (close) => {
      const save = button(project ? "Сохранить" : "Создать проект", { kind: "primary" });
      save.addEventListener("click", () => {
        if (!name.value.trim()) return name.focus();
        return busy(save, async () => {
          const payload = { name: name.value.trim(), description: description.value.trim(), keywords: list(keywords.value),
            kind: kind.value, lifecycle_status: lifecycle.value, discovery_enabled: discovery, needs: list(needs.value, "\n") };
          const profileChanged = !project || payload.description !== (project.description || "")
            || JSON.stringify(payload.keywords) !== JSON.stringify(project.keywords || [])
            || JSON.stringify(payload.needs) !== JSON.stringify(project.needs || []);
          try {
            const saved = project ? await put(`/projects/${project.id}`, payload) : await post("/projects", payload);
            let understood = null;
            let understandError = "";
            if (profileChanged && payload.description.length >= 40 && !["archived"].includes(payload.lifecycle_status)) {
              try { understood = await post(`/projects/${saved.id}/infer-needs`); }
              catch (error) { understandError = errorText(error); } // saving the project is authoritative
            }
            await loadProjects(true);
            toast(understood
              ? `Проект сохранён. StackSignal определил потребности: ${understood.needs.length}.`
              : understandError ? `Проект сохранён. Автопотребности не обновлены: ${understandError}`
              : (project ? "Проект сохранён" : "Проект создан. Поисковые запросы появятся при следующем сборе."),
              { kind: understandError ? "info" : "success" });
            close();
            onSaved?.(saved);
          } catch (error) {
            toast(errorText(error), { kind: "error" });
          }
        });
      });
      return [button("Отмена", { kind: "ghost", onClick: close }), save];
    },
  });
  name.focus();
}

// Reads only dependency files and the README of the project's real folder, proposes stack/needs/keywords;
// nothing is saved until "Применить".
function importSection(project, fields) {
  const path = input({ value: project.stack_path || "", placeholder: "C:\\Users\\вы\\Projects\\мой-проект" });
  let askAi = false;
  const result = h("div", { class: "import-result" });
  const note = h("p", { class: "field-hint" });
  const scan = button("Сканировать", { iconName: "search" });
  scan.addEventListener("click", () => busy(scan, async () => {
    result.replaceChildren();
    note.textContent = askAi ? "Читаю файлы и спрашиваю AI… до минуты." : "Читаю файлы…";
    try {
      const res = await post(`/projects/${project.id}/scan`, { path: path.value.trim(), ai: askAi });
      note.textContent = `Прочитано файлов: ${res.files.length}, зависимостей: ${res.dependencies.length}.` +
        (res.ai_error ? ` AI не ответил (${res.ai_error}) — показано то, что дали сами файлы.` : "") + " Проверьте и примените.";
      renderProposal(project, res, result, fields, note);
    } catch (error) {
      note.textContent = errorText(error);
    }
  }, "Сканирую…"));
  return h("details", { class: "fold import" },
    h("summary", null, "Импорт из папки проекта"),
    h("p", { class: "field-hint" }, "Читаются только файлы зависимостей (requirements, package.json, Gradle, csproj, Unity manifest…) и README. Исходный код и .env — никогда."),
    h("div", { class: "form-row form-row-end" }, field("Папка", path), scan),
    toggle("Спросить AI (описание, ключевые слова, потребности)", false, (v) => (askAi = v)),
    note, result);
}

function renderProposal(project, scan, box, fields, note) {
  const p = scan.proposal;
  const union = (...lists) => {
    const seen = new Set();
    return lists.flat().filter((v) => v && !seen.has(v.toLowerCase()) && seen.add(v.toLowerCase()));
  };
  const stack = textarea({ rows: 2, value: union(project.stack || [], p.stack).join(", ") });
  const needs = textarea({ rows: 2, value: union(project.needs || [], p.needs).join(", ") });
  const keywords = textarea({ rows: 2, value: union(project.keywords || [], p.keywords).join(", ") });
  const description = textarea({ rows: 3, value: p.description || project.description || "" });
  const apply = button("Применить к проекту", { kind: "primary", size: "sm" });
  apply.addEventListener("click", () => busy(apply, async () => {
    const stackList = list(stack.value);
    try {
      await post(`/projects/${project.id}/apply-scan`, {
        path: scan.path, stack: stackList,
        packages: scan.dependencies,
        needs: list(needs.value), keywords: list(keywords.value), description: description.value.trim(),
      });
      fields.keywords.value = keywords.value;
      fields.description.value = description.value;
      fields.needs.value = list(needs.value).join("\n");
      box.replaceChildren();
      let understood = null;
      let understandError = "";
      if (description.value.trim().length >= 40) {
        try { understood = await post(`/projects/${project.id}/infer-needs`); }
        catch (error) { understandError = errorText(error); } // imported profile still stays applied
      }
      note.textContent = understood
        ? `Применено. По профилю автоматически определено потребностей: ${understood.needs.length}.`
        : understandError ? `Профиль применён; автопотребности не обновлены: ${understandError}`
        : "Применено. Релевантность к проекту пересчитается короткими вызовами AI в ближайших сборах.";
      await loadProjects(true);
    } catch (error) {
      toast(errorText(error), { kind: "error" });
    }
  }));
  box.replaceChildren(
    field("Стек — что уже используется", stack),
    field("Потребности — чего не хватает", needs),
    field("Ключевые слова", keywords),
    field("Описание", description),
    h("div", { class: "form-actions" }, apply));
}
