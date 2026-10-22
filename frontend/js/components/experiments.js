import { get, post, put } from "../api.js";
import { h } from "../dom.js";
import { errorText } from "../i18n.js";
import { alertBox, busy, button, field, openModal, select, textarea, toast, toggle } from "../ui.js";
import { loadProjects } from "../store.js";

const STATUS = { draft: "Задание готово", running: "Проверяю", completed: "Проверка завершена", cancelled: "Отменена" };
const OUTCOME = { useful: "Полезно", not_useful: "Не подошло", inconclusive: "Недостаточно данных" };

export function prepareExperiment(need, candidate, reload) {
  const prepare = button("Подготовить проверку", { kind: "secondary", size: "sm", disabled: need.readiness !== "ready" || candidate.constraint_status === "conflict" });
  prepare.addEventListener("click", () => busy(prepare, async () => {
    try {
      const row = await post(`/needs/${need.id}/experiments`, { item_id: candidate.id });
      showExperiment(row, reload);
      reload();
    } catch (error) { toast(errorText(error), { kind: "error" }); }
  }, "Готовлю…"));
  return prepare;
}

export function experimentHistory(rows, reload) {
  if (!rows.length) return null;
  return h("div", { class: "stack compact-stack section-tight" }, h("h3", { class: "small" }, "Проверки и результаты"), rows.map(row =>
    h("div", { class: "panel panel-pad experiment-card", dataset: { experimentId: row.id } },
      h("b", { class: "small" }, row.candidate_name),
      h("p", { class: "small" }, `${STATUS[row.status]}${row.outcome ? ` · ${OUTCOME[row.outcome]}` : ""}${row.stale ? " · исходный контекст изменился" : ""}`),
      row.result_note && h("p", { class: "small" }, row.result_note),
      row.need_confirmed && h("p", { class: "small" }, "Потребность подтверждена этой проверкой."),
      button("Открыть проверку", { kind: "ghost", size: "sm", onClick: async () => {
        try { showExperiment(await get(`/experiments/${row.id}`), reload); }
        catch (error) { toast(errorText(error), { kind: "error" }); }
      } }))));
}

function showExperiment(row, reload) {
  const active = row.status === "draft" || row.status === "running";
  const brief = textarea({ value: row.brief, rows: 14, readOnly: true });
  const copy = button("Скопировать задание", { kind: "secondary", size: "sm", onClick: async () => {
    try { await navigator.clipboard.writeText(row.brief); toast("Задание скопировано.", { kind: "success" }); }
    catch { brief.focus(); brief.select(); toast("Выделено всё задание — скопируйте его сочетанием Ctrl+C."); }
  } });
  const outcome = select([["inconclusive", OUTCOME.inconclusive], ["useful", OUTCOME.useful], ["not_useful", OUTCOME.not_useful]], row.outcome || "inconclusive");
  const result = textarea({ value: row.result_note, rows: 4, placeholder: "Что проверено, в каких условиях и что получилось" });
  const evidence = textarea({ value: (row.evidence || []).join("\n"), rows: 3, placeholder: "Команды и измерения, ссылки или пути к отчётам — по одному на строку" });
  const decision = select([["", "Оставить текущее решение"], ["adopt", "Использую в проекте"], ["ignore", "Отклонить для проекта"], ["watch", "Наблюдать"], ["hold", "Отложить"]], "");
  let confirmNeed = false;
  const confirm = toggle("Подтвердить закрытие потребности этим результатом", false, value => { confirmNeed = value; });
  confirm.querySelector("input").disabled = row.stale;
  openModal({ title: "Проверка кандидата", wide: true,
    body: h("div", { class: "stack" },
      h("p", null, row.snapshot.candidate.name, " → ", row.snapshot.need.title),
      h("p", { class: "small muted" }, STATUS[row.status]),
      row.stale && alertBox("warn", "Исходный контекст изменился", "Можно сохранить историю проверки. Для подтверждения текущей потребности подготовьте новое задание."),
      h("p", { class: "small muted" }, "Передайте задание Claude или другому агенту в папке проекта. Здесь сохраняются задание и результат; выполнение запускается в вашем агенте."),
      field("Задание для Claude", brief),
      h("div", { class: "section-actions" }, copy,
        h("a", { class: "btn btn-secondary btn-sm", href: `/api/experiments/${row.id}/brief`, download: `experiment-${row.id}.md` }, "Скачать .md")),
      active ? h("div", { class: "form" }, field("Итог проверки", outcome), field("Результат эксперимента", result),
        field("Свидетельства проверки", evidence, "Для итогов «Полезно» и «Не подошло» требуется хотя бы одно свидетельство."),
        confirm, field("Решение для проекта", decision),
        h("p", { class: "small muted" }, "Закрытие потребности доступно для полезного результата при текущих требованиях. Подготовка задания не подтверждает результат."))
        : h("div", { class: "stack" }, row.outcome && h("b", null, OUTCOME[row.outcome]),
          row.result_note && h("p", null, row.result_note), (row.evidence || []).map(value => h("p", { class: "small" }, value)))),
    actions: close => {
      const action = (label, data, kind = "secondary") => {
        const control = button(label, { kind });
        control.addEventListener("click", () => busy(control, async () => {
          try {
            await put(`/experiments/${row.id}`, typeof data === "function" ? data() : data);
            await loadProjects(true);
            close(); reload();
            toast("Проверка сохранена.", { kind: "success" });
          } catch (error) { toast(errorText(error), { kind: "error" }); }
        }));
        return control;
      };
      return [button("Закрыть", { kind: "ghost", onClick: close }),
        active && action("Отменить проверку", { status: "cancelled" }, "ghost"),
        row.status === "draft" && !row.stale && action("Начать проверку", { status: "running" }),
        active && action("Записать результат", () => ({ status: "completed", outcome: outcome.value,
          result_note: result.value, evidence: evidence.value.split("\n").map(value => value.trim()).filter(Boolean),
          confirm_need: confirmNeed, decision: decision.value || null }), "primary")].filter(Boolean);
    } });
}
