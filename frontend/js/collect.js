import { post } from "./api.js";
import { count, errorText } from "./i18n.js";
import { emit } from "./store.js";
import { toast } from "./ui.js";

// A collection run is one long request (~10-15 minutes). The header button and the settings page share
// this, so starting it twice is impossible from the UI and the backend's 409 is a readable message.

export const collectState = { running: false };

export async function runCollection() {
  if (collectState.running) return;
  collectState.running = true;
  emit("collect-state", true);
  toast("Сбор запущен. Обычно 10–15 минут — можно продолжать работать.", { timeout: 6000 });
  try {
    const run = await post("/collect/run");
    const enrich = (run.stages || []).find((s) => s.name === "enrich");
    toast(`Сбор завершён: ${count(run.items_found, "находка", "находки", "находок")}, новых ${run.items_new}` +
      (enrich && "ai" in enrich ? `, AI за этот сбор: ${enrich.ai}` : ""), { kind: run.status === "completed" ? "success" : "info", timeout: 9000 });
    emit("collect-done", run);
  } catch (error) {
    toast(errorText(error), { kind: error.status === 409 ? "info" : "error" });
  } finally {
    collectState.running = false;
    emit("collect-state", false);
  }
}
