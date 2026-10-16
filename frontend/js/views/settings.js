import { get, post } from "../api.js";
import { h, mount } from "../dom.js";
import { T, count, duration, errorText, fmtDateTime, fmtNum, timeAgo } from "../i18n.js";
import { icon } from "../icons.js";
import { collectState, runCollection } from "../collect.js";
import { health, on } from "../store.js";
import { alertBox, busy, button, empty, pageHead, sectionHead, segmented, skeletonGrid, subTabs, toast } from "../ui.js";
import { applyTheme, currentTheme } from "../theme.js";
import { analysisCoverage } from "../components/analysis-coverage.js";

const TABS = [["collection", "Сбор данных", "radar"], ["digest", "Дайджест", "telegram"], ["appearance", "Внешний вид", "sun"], ["guide", "Справка", "book"]];

export async function render(root, [tab = "collection"] = []) {
  const body = h("div");
  mount(root, pageHead("Настройки"),
    subTabs(TABS.map(([key, label, iconName]) => [key, label, `#/settings/${key}`, iconName]), tab), body);
  const views = { collection, digest, appearance, guide };
  await (views[tab] || collection)(body);
}

// ---------- collection runs ----------

const STAGE_COLORS = {
  collect: "var(--c1)", discovery: "var(--c2)", gate_and_score: "var(--c3)", entity_linking: "var(--c4)", enrich: "var(--ai)",
  opportunities: "var(--c5)", capabilities: "var(--c6)", embeddings: "var(--c7)", housekeeping: "var(--c8)",
};

async function collection(body) {
  body._cleanup?.forEach((off) => off());
  const runBtn = button(collectState.running ? "Идёт сбор…" : "Запустить сбор", { kind: "primary", iconName: "play", disabled: collectState.running });
  runBtn.addEventListener("click", runCollection);
  body._cleanup = [
    on("collect-state", (running) => {
      runBtn.disabled = running;
      runBtn.lastChild.textContent = running ? "Идёт сбор…" : "Запустить сбор";
    }),
    on("collect-done", () => body.isConnected && collection(body)),
  ];
  const gateway = h("div");
  const coverage = h("div", { class: "section-tight" });
  const runs = h("div");
  mount(body, gateway, coverage,
    h("section", { class: "section" }, sectionHead("Последние сборы", { actions: runBtn,
      hint: "каждый день автоматически в DAILY_RUN_TIME, если компьютер включён" }), runs));
  renderGateway(gateway);
  mount(runs, skeletonGrid(3, "skeleton-row"));
  try {
    const [list, stats] = await Promise.all([get("/collect/runs?limit=10"), get("/stats")]);
    mount(coverage, analysisCoverage(stats));
    mount(runs, list.length ? h("div", { class: "stack" }, list.map(runCard)) : empty("radar", "Сборов ещё не было", "Запустите первый — он займёт около 10 минут."));
  } catch (error) {
    mount(runs, alertBox("error", "Не удалось загрузить сборы", errorText(error)));
  }
}

function renderGateway(box) {
  const value = health.value;
  const recheck = button("Проверить", { kind: "ghost", size: "sm", iconName: "refresh" });
  recheck.addEventListener("click", () => busy(recheck, async () => {
    try { health.value = await get("/health"); } catch (error) { toast(errorText(error), { kind: "error" }); }
    renderGateway(box);
  }));
  let tone = "off", title = "AI выключен — режим ключевых слов", text = "Нет AI_API_KEY или AI_ENABLED=0. Всё работает, но без сводок, причин, брифингов и умного чата.";
  if (value?.llm_enabled) {
    const g = value.ai_gateway;
    if (g?.ready) { tone = "ok"; title = "AI-шлюз готов"; text = "Находки читаются и оцениваются AI, чат отвечает по существу."; }
    else { tone = "bad"; title = `AI-шлюз не готов: ${T.gatewayCode[g?.code] || g?.code || "нет ответа"}`;
      text = "Пока шлюз не работает, сбор идёт в режиме ключевых слов. Проверьте http://127.0.0.1:8787/readyz; при «provider_recovery_required» или «deadline_exceeded» помогает перезапуск шлюза, при «authentication_required» — npm run browser:login."; }
    const dispatch = g?.dispatch;
    if (dispatch) {
      text = `Общий бюджет: ${dispatch.used_day ?? 0} из ${dispatch.limits?.per_day ?? "—"} запросов за день. `;
      if (dispatch.hold) text += `Пауза после: ${T.gatewayCode[dispatch.hold.reason] || dispatch.hold.reason}. `;
      if (dispatch.next_allowed_at > Date.now()) text += `Следующее окно: ${new Date(dispatch.next_allowed_at).toLocaleString("ru-RU")}. `;
      text += "Необработанные находки остаются в очереди.";
    }
  }
  mount(box, h("div", { class: `gateway gateway-${tone}` },
    h("span", { class: "gateway-dot" }), h("div", { class: "grow" }, h("div", { class: "gateway-title" }, title), h("div", { class: "muted small" }, text)),
    recheck));
}

function runCard(run) {
  const stages = run.stages || [];
  const total = stages.reduce((sum, s) => sum + (s.duration_s || 0), 0);
  const enrich = stages.find((s) => s.name === "enrich");
  const tone = run.status === "completed" ? "ok" : run.status === "failed" ? "bad" : "warn";
  const failed = stages.find((s) => s.status === "error");
  return h("article", { class: "run" },
    h("div", { class: "run-head" },
      h("span", { class: `runline-status runline-${tone}` }, h("i", { class: "dot" }), T.runStatus[run.status] || run.status),
      h("b", null, fmtDateTime(run.started_at)),
      h("span", { class: "faint xs" }, timeAgo(run.started_at)),
      h("span", { class: "grow" }),
      h("span", { class: "run-num" }, h("b", { class: "num" }, fmtNum(run.items_found)), " найдено"),
      h("span", { class: "run-num" }, h("b", { class: "num" }, fmtNum(run.items_new)), " новых"),
      enrich && "ai" in enrich && h("span", { class: "run-num" + (enrich.ai_errors > 0 ? " is-bad" : "") },
        h("b", { class: "num" }, fmtNum(enrich.ai)), " AI за этот сбор"),
      total > 0 && h("span", { class: "run-num" }, h("b", null, duration(total)))),
    stages.length ? h("div", { class: "stagebar", role: "img", "aria-label": "Длительность стадий" },
      stages.map((s) => h("i", { style: { flexGrow: String(Math.max(s.duration_s || 0, total * 0.004)), background: STAGE_COLORS[s.name] || "var(--text-3)" },
        title: `${T.stage[s.name] || s.name}: ${duration(s.duration_s)}${s.status === "error" ? " — ошибка" : ""}` }))) : null,
    stages.length ? h("details", { class: "fold" }, h("summary", null, "Стадии"),
      h("div", { class: "stages" }, stages.map((s) => h("div", { class: "stage" + (s.status === "error" ? " is-bad" : "") },
        h("i", { class: "swatch", style: { background: STAGE_COLORS[s.name] || "var(--text-3)" } }),
        h("span", { class: "grow" }, T.stage[s.name] || s.name,
          s.name === "enrich" && "ai" in s ? h("span", { class: "faint xs" }, ` — AI ${s.ai}, по словам ${s.heuristic}` + (s.ai_errors ? `, сбоев ${s.ai_errors}` : "")) : null),
        h("span", { class: "num faint" }, duration(s.duration_s)))))) : null,
    failed && h("p", { class: "error-text small" }, `Стадия «${T.stage[failed.name] || failed.name}» упала: ${failed.error}`),
    run.error && h("details", { class: "fold" }, h("summary", null, "Сообщения источников"), h("pre", { class: "pre" }, run.error)));
}

// ---------- Telegram digest ----------

async function digest(body) {
  mount(body, skeletonGrid(2, "skeleton-row"));
  let status;
  try {
    status = await get("/digest/status");
  } catch (error) {
    mount(body, alertBox("error", "Не удалось загрузить состояние дайджеста", errorText(error)));
    return;
  }
  const preview = h("div");
  const message = h("p", { class: "muted small" });
  const say = (text) => (message.textContent = text);
  const previewBtn = button("Предпросмотр", { iconName: "search" });
  previewBtn.addEventListener("click", () => busy(previewBtn, async () => {
    try {
      const res = await get("/digest/preview");
      say(res.empty ? "Ничего нового: плановый сбор ничего бы не отправил." : `${count(res.items, "элемент", "элемента", "элементов")}, брифингов: ${res.opportunities}`);
      mount(preview, h("pre", { class: "pre digest-preview" }, res.messages.join("\n\n— следующее сообщение —\n\n")));
    } catch (error) { say(errorText(error)); }
  }));
  const sendBtn = button("Отправить сейчас", { kind: "primary", iconName: "send", disabled: !status.configured });
  sendBtn.addEventListener("click", () => busy(sendBtn, async () => {
    try {
      const res = await post("/digest/send");
      toast(`Отправлено сообщений: ${res.sent} (${res.items} элементов, ${res.opportunities} брифингов)`, { kind: "success" });
      digest(body);
    } catch (error) { say(errorText(error)); }
  }));
  const chatsBtn = button("Узнать мой chat id", { iconName: "telegram", disabled: !status.token_set });
  chatsBtn.addEventListener("click", () => busy(chatsBtn, async () => {
    try {
      const chats = await post("/digest/chats");
      say(chats.length ? `Ваш chat id: ${chats.map((c) => `${c.id} (${c.name || c.type})`).join(", ")} — впишите его в .env как TELEGRAM_CHAT_ID`
        : "Сообщений не найдено: откройте бота в Telegram, нажмите Start, напишите любое слово и повторите.");
    } catch (error) { say(errorText(error)); }
  }));
  const shape = status.token_set && !status.token_shape_ok &&
    alertBox("warn", "TELEGRAM_BOT_TOKEN не похож на токен бота", "Нужна вся строка из BotFather: число, двоеточие и длинная часть — вида 123456789:AAH…");
  const next = status.next_run ? fmtDateTime(status.next_run) : "не запланирован";
  mount(body,
    h("section", { class: "panel panel-pad" },
      status.configured
        ? [h("h2", { class: "section-title" }, "Подключено к Telegram"),
          h("p", { class: "muted" }, `После ежедневного сбора в ${status.daily_run_time} (следующий: ${next}) приходит до ${status.max_items} элементов с релевантностью от ${Math.round(status.min_relevance * 100)}% и свежие брифинги. Ничего нового — нет сообщения.`),
          !status.enabled && alertBox("info", "Сейчас выключено", "DIGEST_ENABLED=0 в .env.")]
        : [h("h2", { class: "section-title" }, "Не настроено — четыре шага, один раз"),
          h("ol", { class: "steps" },
            h("li", null, "В Telegram откройте ", h("b", null, "@BotFather"), ", отправьте /newbot и скопируйте токен."),
            h("li", null, "Откройте своего нового бота и нажмите ", h("b", null, "Start"), "."),
            h("li", null, "Впишите токен в .env как TELEGRAM_BOT_TOKEN=… и перезапустите StackSignal."),
            h("li", null, "Нажмите «Узнать мой chat id», впишите число как TELEGRAM_CHAT_ID=… и перезапустите ещё раз.")),
          h("p", { class: "faint small" }, `Токен ${status.token_set ? "✓" : "✗"} · chat id ${status.chat_id_set ? "✓" : "✗"}`)],
      shape,
      h("div", { class: "form-actions form-actions-start" }, previewBtn, sendBtn, chatsBtn),
      message),
    preview,
    h("section", { class: "section" }, sectionHead("Недавние дайджесты", { count: status.recent.length }),
      status.recent.length ? h("div", { class: "panel" }, status.recent.map((r) =>
        h("div", { class: "change-row" }, icon(r.ok ? "check" : "alert", { size: 16 }),
          h("span", { class: "grow" + (r.ok ? "" : " error-text") }, r.ok ? `${r.items} элементов, ${r.opportunities} брифингов` : T.errors[r.error] || r.error),
          h("span", { class: "faint xs" }, `${fmtDateTime(r.sent_at)} · ${r.trigger === "manual" ? "вручную" : "по расписанию"}`))))
        : empty("telegram", "Ещё ничего не отправлялось")));
}

// ---------- appearance ----------

function appearance(body) {
  mount(body, h("section", { class: "panel panel-pad" },
    h("h2", { class: "section-title" }, "Тема"),
    h("p", { class: "muted small" }, "«Как в системе» следует настройке Windows."),
    segmented([["system", "Как в системе", "monitor"], ["light", "Светлая", "sun"], ["dark", "Тёмная", "moon"]], currentTheme(), applyTheme, { label: "Тема" })));
}

// ---------- guide ----------

async function guide(body) {
  let stats = null;
  try { stats = await get("/stats"); } catch { /* the guide still reads fine */ }
  const p = (...children) => h("p", null, ...children);
  mount(body, h("article", { class: "prose" },
    stats && h("div", { class: "callout" }, icon("info", { size: 16 }), h("div", null,
      `Сейчас: ${fmtNum(stats.items)} находок, из них AI прочитал ${fmtNum(stats.ai_analyzed)} · проектов ${stats.projects} · способностей ${stats.capabilities} · ваших голосов ${stats.votes} и заметок ${stats.notes}.`)),
    h("h2", null, "Как устроен StackSignal"),
    p("Он следит за GitHub, Hugging Face, arXiv, npm и PyPI и говорит, что из нового важно для ", h("b", null, "ваших"), " проектов и почему. Не «что популярно», а «что изменилось и чем я могу воспользоваться»."),
    h("h3", null, "10 минут в день"),
    h("ol", null,
      h("li", null, h("b", null, "Сегодня"), " — всё главное на одном экране: работает ли сбор и AI, новое для проектов, что ждёт решения, брифинги, обновления стека."),
      h("li", null, "Откройте находку — справа появятся подробности: почему она важна каждому проекту, 👍/👎, решения, заметки."),
      h("li", null, "В ", h("b", null, "Проектах"), " на вкладке «Решения» — доска по проекту: входящие, оцениваю, пробую, использую, отклонено."),
      h("li", null, "Перед техническим решением спросите ", h("b", null, "Чат"), " или загляните в «Обзор → Способности».")),
    h("h3", null, "Как читать карточку"),
    h("ul", null,
      h("li", null, h("b", null, "AI"), " / ", h("b", null, "по словам"), " — прочитал ли находку AI или она оценена только по совпадению ключевых слов (грубо: частое слово может дать высокий балл)."),
      h("li", null, h("b", null, "Рост"), " — как быстро растёт внимание прямо сейчас. ", h("b", null, "Зрелость"), " — насколько безопасно на это опираться."),
      h("li", null, "Процент у проекта — релевантность. 👍 поднимает её минимум до 90%, 👎 убирает находку из подборок проекта; повторное нажатие снимает оценку.")),
    h("h3", null, "Статусы"),
    h("dl", { class: "facts" }, Object.entries(T.status).map(([k, v]) => [h("dt", null, v), h("dd", null, T.statusHint[k])])),
    p("AI сам может предложить максимум «Оценить». «Пробую» и «Использую» означают ваши действия, поэтому их ставите только вы."),
    h("h3", null, "Если AI молчит"),
    p("Без AI-шлюза всё работает в режиме ключевых слов. Если на «Сегодня» появилось предупреждение, что AI ничего не прочитал, откройте «Настройки → Сбор данных»: там видно состояние шлюза и что делала каждая стадия сбора.")));
}
