import { get, post } from "../api.js";
import { h, mount } from "../dom.js";
import { T, errorText } from "../i18n.js";
import { loadProjects } from "../store.js";
import { alertBox, busy, button, closeModal, empty, field, input, openModal, select, textarea, toast, toggle } from "../ui.js";

const terms = (text, separator = ",") => text.split(separator).map(s => s.trim()).filter(Boolean);

export function openProjectDirectory({ onSaved } = {}) {
  const path = input({ value: localStorage.getItem("project-directory-path") || "", placeholder: "Папка Projects" });
  const results = h("div", { class: "stack" });
  const note = h("p", { class: "field-hint", role: "status" });
  const lifecycle = select(Object.entries(T.lifecycle), "active", { "aria-label": "Состояние новых проектов" });
  let discovery = true;
  let candidates = [];
  let scannedPath = "";
  let importing = false;
  let add;
  const update = () => {
    const selected = candidates.filter(c => c.checkbox.checked).length;
    add.textContent = `Добавить выбранные (${selected})`;
    add.disabled = importing || !selected;
  };
  const scan = button("Сканировать", { iconName: "search" });
  scan.addEventListener("click", () => busy(scan, async () => {
    candidates = [];
    scannedPath = "";
    update();
    results.replaceChildren();
    path.disabled = true;
    note.textContent = "Ищу проекты и читаю README и файлы зависимостей…";
    try {
      const data = await post("/projects/scan-directory", { path: path.value.trim() });
      scannedPath = data.path;
      path.value = data.path;
      localStorage.setItem("project-directory-path", data.path);
      const fresh = data.projects.filter(p => !p.existing_project);
      note.textContent = `Найдено: ${data.projects.length}. Новых: ${fresh.length}. Уже добавлено: ${data.projects.length - fresh.length}.` +
        (data.skipped.length ? ` Пропущено папок: ${data.skipped.length}.` : "");
      candidates = fresh.map(p => candidate(p, update));
      const byPath = new Map(candidates.map(c => [c.project.path, c]));
      const all = input({ type: "checkbox", "aria-label": "Выбрать все новые проекты", onChange: () => {
        for (const c of candidates) c.checkbox.checked = all.checked;
        update();
      } });
      for (const c of candidates) c.checkbox.addEventListener("change", () => {
        const count = candidates.filter(p => p.checkbox.checked).length;
        all.checked = count === candidates.length;
        all.indeterminate = count > 0 && count < candidates.length;
      });
      mount(results,
        data.truncated && alertBox("warn", "Показана часть папок", `За один раз проверяется до ${data.limit} папок. Укажите более узкую папку для остальных проектов.`),
        fresh.length > 0 && h("div", { class: "form" },
          h("label", { class: "directory-select" }, all, "Выбрать все новые проекты"),
          field("Состояние новых проектов", lifecycle),
          toggle("Искать новые материалы для добавленных проектов", discovery, value => (discovery = value))),
        data.projects.map(p => byPath.get(p.path)?.node || existingRow(p)),
        !data.projects.length && empty("folder", "Проекты не найдены", "В подпапках должны быть README или файлы зависимостей."));
      update();
    } catch (error) {
      note.textContent = errorText(error);
    } finally {
      path.disabled = false;
    }
  }, "Сканирую…"));
  path.addEventListener("input", () => {
    candidates = [];
    scannedPath = "";
    results.replaceChildren();
    note.textContent = "";
    update();
  });
  path.addEventListener("keydown", event => {
    if (event.key === "Enter" && !scan.disabled) { event.preventDefault(); scan.click(); }
  });
  const body = h("div", { class: "form" },
    h("p", { class: "muted small" }, "Каждая подпапка проверяется как отдельный проект. Выберите нужные и проверьте профиль перед добавлением."),
    h("div", { class: "form-row form-row-end" }, field("Общая папка проектов", path), scan),
    h("p", { class: "field-hint" }, "Читаются README и файлы зависимостей. Исходный код и .env не читаются. AI для сканирования не нужен."),
    note, results);
  openModal({
    title: "Добавить проекты из папки", body, wide: true,
    actions: close => {
      add = button("Добавить выбранные (0)", { kind: "primary", disabled: true });
      add.addEventListener("click", async () => {
        const selected = candidates.filter(c => c.checkbox.checked);
        const invalid = selected.find(c => !c.name.value.trim());
        if (invalid) { invalid.details.open = true; invalid.name.focus(); return; }
        importing = true;
        scan.disabled = true;
        path.disabled = true;
        try {
          await busy(add, async () => {
            const data = await post("/projects/import-directory", { path: scannedPath,
              projects: selected.map(c => ({
                path: c.project.path, name: c.name.value.trim(), description: c.description.value.trim(),
                keywords: terms(c.keywords.value), needs: terms(c.needs.value, "\n"),
                stack: terms(c.stack.value), packages: c.project.dependencies,
                kind: c.kind.value,
                lifecycle_status: lifecycle.value, discovery_enabled: discovery && lifecycle.value !== "archived",
              })) });
            await loadProjects(true);
            toast(`Добавлено проектов: ${data.created.length}.` + (data.skipped.length ? ` Уже были добавлены: ${data.skipped.length}.` : ""), { kind: "success" });
            close();
            onSaved?.();
          }, "Добавляю…");
        } catch (error) {
          toast(errorText(error), { kind: "error" });
        } finally {
          importing = false;
          scan.disabled = false;
          path.disabled = false;
          update();
        }
      });
      return [button("Закрыть", { kind: "ghost", onClick: close }), add];
    },
  });
  if (!path.value) get("/projects/scan-directory").then(data => {
    if (body.isConnected && !path.value) path.value = data.path;
  }).catch(() => {}); // an empty path also uses the server's default Projects folder
}

function existingRow(project) {
  const existing = project.existing_project;
  return h("div", { class: "panel panel-pad directory-existing" },
    h("b", null, project.name),
    h("div", { class: "faint xs directory-path" }, project.path),
    h("a", { href: `#/projects/${encodeURIComponent(existing.slug)}`, onClick: closeModal }, `Уже добавлен: ${existing.name}`));
}

function candidate(project, onChange) {
  const checkbox = input({ type: "checkbox", "aria-label": `Добавить ${project.name}`, onChange });
  const name = input({ value: project.name, maxlength: 200 });
  const description = textarea({ rows: 3, value: project.proposal.description, maxlength: 10000 });
  const keywords = textarea({ rows: 2, value: project.proposal.keywords.join(", "), "aria-label": "Ключевые слова", placeholder: "Какие технологии и возможности искать" });
  const needs = textarea({ rows: 2, value: project.proposal.needs.join("\n"), placeholder: "Чего не хватает — по строке на пункт" });
  const stack = textarea({ rows: 2, value: project.proposal.stack.join(", ") });
  const kind = select(Object.entries(T.contextKind), "project", { "aria-label": "Тип добавляемого контекста" });
  const details = h("details", { class: "fold" }, h("summary", null, "Проверить профиль"),
    h("div", { class: "form" }, field("Название проекта", name), field("Тип контекста", kind), field("Описание проекта", description),
      field("Ключевые слова", keywords, "Через запятую: возможности, которые нужны проекту."),
      field("Потребности", needs), field("Текущий стек", stack),
      h("p", { class: "field-hint" }, `Файлы: ${project.files.join(", ")}`)));
  const node = h("article", { class: "panel panel-pad directory-candidate" },
    h("label", { class: "directory-select" }, checkbox, h("b", null, project.name)),
    h("div", { class: "faint xs directory-path" }, project.path),
    h("p", { class: "small" }, project.proposal.description || "Без описания — уточните профиль перед добавлением."),
    project.proposal.stack.length > 0 && h("p", { class: "faint xs" }, "Стек: ", project.proposal.stack.join(" · ")),
    details);
  return { project, checkbox, name, description, keywords, needs, stack, kind, details, node };
}
