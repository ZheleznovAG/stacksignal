import { get } from "./api.js";
import { collectState, runCollection } from "./collect.js";
import { h, mount } from "./dom.js";
import { T } from "./i18n.js";
import { icon } from "./icons.js";
import { health, loadProjects, on } from "./store.js";
import { closeDrawer, closeModal } from "./ui.js";
import * as chat from "./views/chat.js";
import * as explore from "./views/explore.js";
import * as projects from "./views/projects.js";
import * as settings from "./views/settings.js";
import * as today from "./views/today.js";

const VIEWS = { today, projects, explore, chat, settings };
const NAV = [["today", "today"], ["projects", "projects"], ["explore", "explore"], ["chat", "chat"], ["settings", "settings"]];

// ---------- header ----------

const nav = document.getElementById("nav");
mount(nav, NAV.map(([key, iconName]) => h("a", { href: `#/${key}`, dataset: { view: key } }, icon(iconName, { size: 18 }), h("span", null, T.nav[key]))));

const pill = document.getElementById("health");
function paintHealth() {
  const value = health.value;
  let tone = "unknown", label = "AI…", title = "Проверяю AI-шлюз";
  if (value === "down") { tone = "bad"; label = "Нет связи"; title = "Сервер StackSignal не отвечает"; }
  else if (value && !value.llm_enabled) { tone = "off"; label = "AI выкл"; title = "AI не настроен — режим ключевых слов"; }
  else if (value?.ai_gateway?.ready) { tone = "ok"; label = "AI готов"; title = "AI-шлюз готов"; }
  else if (value) { tone = "bad"; label = "AI не готов"; title = `AI-шлюз: ${T.gatewayCode[value.ai_gateway?.code] || value.ai_gateway?.code || "нет ответа"}`; }
  pill.className = `health-pill health-${tone}`;
  pill.title = title;
  mount(pill, h("i", { class: "dot" }), h("span", null, label));
}
async function refreshHealth() {
  try { health.value = await get("/health"); } catch { health.value = "down"; }
  paintHealth();
}
pill.addEventListener("click", () => (location.hash = "#/settings/collection"));

const runBtn = document.getElementById("run");
function paintRun() {
  runBtn.disabled = collectState.running;
  runBtn.title = collectState.running ? "Сбор идёт, обычно 10–15 минут" : "Запустить сбор: мировые списки, поиск по проектам, AI-анализ";
  mount(runBtn, collectState.running ? h("span", { class: "spinner", "aria-hidden": "true" }) : icon("play", { size: 15 }),
    h("span", { class: "run-label" }, collectState.running ? "Идёт сбор…" : "Собрать"));
}
runBtn.addEventListener("click", runCollection);
on("collect-state", paintRun);

// ---------- routing ----------

const viewEl = document.getElementById("view");

function parse() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  return { name: VIEWS[parts[0]] ? parts[0] : "today", params: parts.slice(1) };
}

async function route() {
  const { name, params } = parse();
  closeDrawer();
  closeModal();
  nav.querySelectorAll("a").forEach((a) => (a.dataset.view === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  document.title = `${T.nav[name]} · StackSignal`;
  // Each navigation renders into its own container, so a slow previous view can never overwrite this one.
  const root = h("div", { class: `page page-${name}` });
  mount(viewEl, root);
  window.scrollTo(0, 0);
  await VIEWS[name].render(root, params);
}

window.addEventListener("hashchange", route);
on("collect-done", () => {
  loadProjects(true).catch(() => {});
  if (["today", "projects", "explore"].includes(parse().name)) route();
});

// ---------- start ----------

paintHealth();
paintRun();
health.ready = refreshHealth(); // the home screen waits for it, so its AI warning is right on first paint
setInterval(refreshHealth, 60000);
window.addEventListener("focus", refreshHealth);
loadProjects().catch(() => {});
if (!location.hash) history.replaceState(null, "", "#/today");
route();
