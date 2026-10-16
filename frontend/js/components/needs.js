import { get, post, put } from "../api.js";
import { h, mount } from "../dom.js";
import { errorText, T, pct } from "../i18n.js";
import { alertBox, busy, button, empty, field, input, openModal, sectionHead, select, textarea, toast, toggle } from "../ui.js";
import { loadProjects } from "../store.js";
import { openItem } from "./item-drawer.js";
import { quickDecision } from "./quick-decision.js";

const STATES = { open: "Открыта", evaluating: "Проверяю", satisfied: "Результат подтверждён", deferred: "Отложена", dropped: "Отказался" };
const COVERAGE = { pending: "Оценка не завершена", candidates: "Есть кандидаты для проверки", evaluating: "Идёт проверка",
  confirmed: "Есть подтверждённый результат", result_stale: "Результат записан для прежних условий — нужна повторная проверка",
  blocked: "Сначала нужно закрыть базовую потребность", no_match: "Среди проверенных кандидатов подходящих нет" };
const CONSTRAINTS = { unknown: "Ограничения ещё не проверены", supported: "Описание поддерживает требования; нужна проверка", conflict: "Найдено противоречие требованиям" };

export async function needsTab(body, project) {
  const reload = () => body.isConnected && needsTab(body, project);
  try {
    const [rows, plan] = await Promise.all([get(`/needs?project_id=${project.id}`), get("/needs/plan")]);
    const analyze = button("Оценить следующую пару находок", { kind: "secondary", disabled: !plan.pending_pairs });
    analyze.addEventListener("click", () => busy(analyze, async () => {
      try {
        const result = await post("/needs/analyze");
        toast(result.error_code ? `Анализ ожидает: ${T.gatewayCode[result.error_code] || result.error_code}`
          : `Сохранено оценок: ${result.saved}.`, { kind: result.error_code ? "info" : "success" });
        reload();
      } catch (error) { toast(errorText(error), { kind: "error" }); }
    }, "Оцениваю…"));
    const understand = button("Переосмыслить проект", { kind: "primary", iconName: "sparkle" });
    understand.addEventListener("click", () => busy(understand, async () => {
      try {
        const result = await post(`/projects/${project.id}/infer-needs`);
        await loadProjects(true);
        toast(`Потребности: ${result.needs.length}; зависимостей: ${result.dependency_count || 0}; новых поисковых запросов: ${result.queries_added}.`, { kind: "success" });
        reload();
      } catch (error) { toast(errorText(error), { kind: "error" }); }
    }, "Анализирую…"));
    mount(body, sectionHead("Потребности и результаты", { actions: [understand, button("Добавить вручную", {
      kind: "secondary", onClick: () => editNeed(project, null, reload) })] }),
      h("p", { class: "muted small section-tight" }, "Основной источник — описание и текущий стек проекта: StackSignal сам выводит, чего не хватает, задаёт приоритет и ищет технологии под эти задачи. Ручные потребности и оценки только уточняют картину."),
      rows.length && workOrder(rows),
      rows.some(n => n.pilot) && h("div", { class: "panel panel-pad section-tight" },
        h("p", null, `Пилот: ${plan.candidate_items} находок, ${plan.pending_pairs} оценок в очереди, ${plan.batches_required} пакетов.`),
        h("p", { class: "faint small" }, "Один запуск обрабатывает до двух находок в пределах общего бюджета шлюза."), analyze),
      rows.length ? h("div", { class: "stack" }, rows.map(n => needCard(n, project, reload)))
        : empty("search", "Потребностей пока нет", "Запишите конкретную задачу, ограничения и критерий проверки."));
  } catch (error) { mount(body, alertBox("error", "Не удалось загрузить потребности", errorText(error))); }
}

function workOrder(rows) {
  const ready = rows.filter(n => n.readiness === "ready").sort((a, b) =>
    (b.effective_priority || b.priority) - (a.effective_priority || a.priority) || a.depth - b.depth);
  const blocked = rows.filter(n => n.readiness === "blocked").sort((a, b) => a.depth - b.depth || b.priority - a.priority);
  return h("section", { class: "panel panel-pad section-tight" },
    sectionHead("Что делать сначала"),
    ready.length
      ? h("div", { class: "stack compact-stack" }, ready.slice(0, 5).map(n => h("div", { class: "small" },
          h("b", null, n.title),
          n.unlocks?.length ? h("span", { class: "faint" }, ` → открывает: ${n.unlocks.map(x => x.title).join(", ")}`) : null)))
      : h("p", { class: "muted small" }, "Нет открытых готовых потребностей."),
    blocked.length ? h("details", { class: "section-tight" },
      h("summary", { class: "small" }, `Позже, после зависимостей (${blocked.length})`),
      blocked.map(n => h("p", { class: "faint small" }, `${n.title} ← ${n.blocked_by.map(x => x.title).join(", ")}`))) : null);
}

function needCard(need, project, reload) {
  const candidates = need.candidates || [];
  return h("section", { class: "panel panel-pad need-card", dataset: { needId: need.id } },
    sectionHead(need.title, { actions: button("Изменить / записать результат", { kind: "ghost", size: "sm", onClick: () => editNeed(project, need, reload) }) }),
    h("div", { class: "small" }, `${STATES[need.state]} · приоритет ${need.priority}/3 · ${COVERAGE[need.coverage]}`),
    need.inference_rationale && h("p", { class: "small" }, "Почему нужно: ", need.inference_rationale),
    need.gap && h("p", { class: "muted small" }, `Пробел проекта: ${need.gap.title}${need.gap.subsystem?.title ? ` · ${need.gap.subsystem.title}` : ""}`,
      need.gap.goals?.length ? ` · цель: ${need.gap.goals.map(g => g.title).join(", ")}` : ""),
    need.depends_on?.length > 0 && h("p", { class: "small" }, "Сначала: ", need.depends_on.map(x => x.title).join(" → ")),
    need.blocked_by?.length > 0 && h("p", { class: "warn small" }, "Заблокировано: ", need.blocked_by.map(x => x.title).join(", ")),
    need.unlocks?.length > 0 && h("p", { class: "muted small" }, "Открывает дальше: ", need.unlocks.map(x => x.title).join(", ")),
    need.constraints && h("p", { class: "muted small" }, "Ограничения: ", need.constraints),
    need.acceptance_criteria && h("p", { class: "muted small" }, "Критерий: ", need.acceptance_criteria),
    need.result_note && h("p", null, "Записанный результат: ", need.result_note),
    h("p", { class: "faint small" }, `Оценено ${need.assessed_count} из ${need.candidate_count} кандидатов. `,
      !need.pilot ? "Автоматическая оценка в пилоте выключена." : "AI не проверяет работу на вашем устройстве."),
    candidates.length > 0 && h("details", { class: "need-candidates" },
      h("summary", null, `Кандидаты (${candidates.length})`),
      candidates.map(c => h("article", { class: "inbox-candidate" },
        button(c.name, { kind: "ghost", onClick: () => openItem(c.id) }),
        h("p", { class: "small" }, c.summary),
        h("p", { class: "faint small" }, c.analysis_status === "stale" ? "Оценка устарела после изменения требований."
          : c.score === null ? "Кандидат по словам, ещё не оценён." : `${pct(c.score)} · ${CONSTRAINTS[c.constraint_status]}`),
        c.rationale && h("p", { class: "small" }, c.rationale),
        c.evidence?.length > 0 && h("blockquote", { class: "small" }, c.evidence.join(" · ")),
        c.provenance?.provider && h("p", { class: "faint xs" }, `Оценка: ${c.provenance.provider}`),
        quickDecision(c, { project_slug: need.project_slug, project_name: need.project_name }, reload)))));
}

function editNeed(project, need, reload) {
  const title = input({ value: need?.title || "", placeholder: "Какую задачу нужно решить?" });
  const constraints = textarea({ rows: 3, value: need?.constraints || "" });
  const criteria = textarea({ rows: 3, value: need?.acceptance_criteria || "" });
  const terms = input({ value: (need?.search_terms || []).join(", "), placeholder: "offline speech recognition, android" });
  const priority = select([["3", "Высокий"], ["2", "Обычный"], ["1", "Низкий"]], String(need?.priority || 2));
  const state = select(Object.entries(STATES), need?.state || "open");
  const result = textarea({ rows: 3, value: need?.result_note || "", placeholder: "Что проверено, что получилось и при каких условиях" });
  const chosen = select([["", "Без выбранной находки"], ...(need?.candidates || []).map(c => [String(c.id), c.name]),
    ...(need?.confirmed_item_id && !(need.candidates || []).some(c => c.id === need.confirmed_item_id)
      ? [[String(need.confirmed_item_id), `Находка #${need.confirmed_item_id}`]] : [])], String(need?.confirmed_item_id || ""));
  let pilot = need?.pilot || false;
  openModal({ title: need ? "Потребность и результат" : "Новая потребность",
    body: h("div", { class: "form" }, field("Задача", title), field("Ограничения", constraints), field("Критерий проверки", criteria),
      field("Поисковые слова", terms, "Через запятую, до 12 конкретных терминов."), field("Приоритет", priority),
      toggle("Включить в пилот AI", pilot, value => { pilot = value; }),
      need && [field("Состояние потребности", state), field("Результат проверки", result), field("Выбранная находка", chosen)]),
    actions: close => {
      const save = button("Сохранить", { kind: "primary" });
      save.addEventListener("click", () => busy(save, async () => {
        try {
          const data = { constraints: constraints.value, acceptance_criteria: criteria.value,
            search_terms: terms.value.split(",").map(t => t.trim()).filter(Boolean), priority: Number(priority.value), pilot };
          if (need) await put(`/needs/${need.id}`, { ...data, title: title.value, state: state.value, result_note: result.value,
            confirmed_item_id: chosen.value ? Number(chosen.value) : null });
          else await post(`/projects/${project.id}/needs`, { ...data, title: title.value });
          await loadProjects(true);
          close(); reload();
        } catch (error) { toast(errorText(error), { kind: "error" }); }
      }));
      return [button("Отмена", { kind: "ghost", onClick: close }), save];
    } });
}

export async function loadNeedSummary(box) {
  try {
    const rows = await get("/needs?pilot_only=true");
    if (!rows.length || !box.isConnected) return;
    mount(box, sectionHead("Потребности: что ещё проверить"), h("div", { class: "grid grid-tiles" }, rows.map(n =>
      h("a", { class: "tile", href: `#/projects/${encodeURIComponent(n.project_slug)}/needs` },
        h("b", null, n.project_name), h("p", null, n.title), h("p", { class: "muted small" }, COVERAGE[n.coverage]),
        h("span", { class: "faint xs" }, `Оценено ${n.assessed_count} из ${n.candidate_count}`)))));
  } catch (error) { mount(box, alertBox("warn", "Потребности недоступны", errorText(error))); }
}
