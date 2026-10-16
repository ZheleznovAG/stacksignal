import { post } from "../api.js";
import { h, mount } from "../dom.js";
import { errorText } from "../i18n.js";
import { icon } from "../icons.js";
import { button } from "../ui.js";
import { openItem } from "../components/item-drawer.js";

// The conversation survives switching screens (module state) and a reload (sessionStorage).
const STORE = "chat-log";
let log = [];
try { log = JSON.parse(sessionStorage.getItem(STORE) || "[]"); } catch { log = []; }
const save = () => { try { sessionStorage.setItem(STORE, JSON.stringify(log.slice(-40))); } catch { /* private mode */ } };

const SUGGESTIONS = [
  "Что нового для Строки по локальному распознаванию речи?",
  "Чем заменить облачный OCR?",
  "Какие маленькие локальные модели полезнее всего для моих проектов?",
  "Почему я отказался от openjev?",
];

export function render(root) {
  const messages = h("div", { class: "chat-log", "aria-live": "polite" });
  const text = h("textarea", { class: "chat-input", rows: 1, placeholder: "Спросите о находках, проектах, своих решениях…", "aria-label": "Сообщение" });
  const send = button("", { kind: "primary", iconName: "send", title: "Отправить" });
  send.type = "submit";
  const form = h("form", { class: "chat-form", onSubmit: (e) => { e.preventDefault(); ask(text.value); } }, text, send);

  const grow = () => { text.style.height = "auto"; text.style.height = `${Math.min(text.scrollHeight, 180)}px`; };
  text.addEventListener("input", grow);
  text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(text.value); }
  });

  const paint = () => {
    mount(messages, log.length ? log.map(bubble) : intro((q) => ask(q)));
    messages.scrollTop = messages.scrollHeight;
  };

  async function ask(question) {
    question = question.trim();
    if (!question || send.disabled) return;
    const history = log.filter((m) => !m.pending && !m.error).map((m) => ({ role: m.role, content: m.text })).slice(-20);
    log.push({ role: "user", text: question });
    const pending = { role: "assistant", pending: true, text: "" };
    log.push(pending);
    text.value = "";
    grow();
    send.disabled = true;
    paint();
    try {
      const res = await post("/chat", { message: question, history });
      Object.assign(pending, { pending: false, text: res.reply, sources: res.sources || [], keyword: res.ai?.mode === "heuristic" });
    } catch (error) {
      Object.assign(pending, { pending: false, error: true, text: errorText(error) });
    }
    send.disabled = false;
    save();
    paint();
    text.focus();
  }

  const clear = button("Новый разговор", { kind: "ghost", size: "sm", iconName: "refresh", onClick: () => { log = []; save(); paint(); text.focus(); } });
  mount(root, h("div", { class: "chat" },
    h("div", { class: "chat-head" },
      h("div", null, h("h1", { class: "page-title" }, "Чат"),
        h("p", { class: "page-sub" }, "Отвечает по собранному: находкам, вашим голосам, заметкам и решениям — и ссылается на источники.")),
      clear),
    messages, form,
    h("p", { class: "faint xs chat-foot" }, "Enter — отправить, Shift+Enter — новая строка. Ответ идёт через AI-шлюз, обычно 10–40 секунд.")));
  paint();
  const draft = sessionStorage.getItem("chat-draft");
  if (draft) {
    sessionStorage.removeItem("chat-draft");
    text.value = draft;
    grow();
  }
  text.focus();
}

function intro(onPick) {
  return h("div", { class: "chat-intro" },
    h("div", { class: "chat-intro-icon" }, icon("chat", { size: 28 })),
    h("p", null, "Спросите по-русски или по-английски. Например:"),
    h("div", { class: "suggestions" }, SUGGESTIONS.map((q) => h("button", { class: "suggestion", type: "button", onClick: () => onPick(q) }, q))));
}

function bubble(m) {
  if (m.role === "user") return h("div", { class: "msg msg-user" }, m.text);
  if (m.pending) return h("div", { class: "msg msg-ai", "aria-label": "Думаю" }, h("span", { class: "typing" }, h("i"), h("i"), h("i")));
  return h("div", { class: "msg msg-ai" + (m.error ? " msg-error" : "") },
    m.error ? m.text : markdown(m.text),
    m.sources?.length ? h("div", { class: "msg-sources" }, h("span", { class: "faint xs" }, "Источники:"),
      m.sources.map((s) => h("button", { class: "chip-link", type: "button", title: s.status, onClick: () => openItem(s.id) }, s.name))) : null,
    m.keyword && h("div", { class: "msg-note" }, icon("info", { size: 14 }), "AI недоступен — показаны совпадения по ключевым словам"));
}

// Just enough Markdown for chat answers: paragraphs, bullet/numbered lists, **bold**, `code`.
function markdown(source) {
  const out = h("div", { class: "md" });
  let list = null;
  for (const raw of source.split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag); out.append(list); }
      list.append(h("li", null, inline((bullet || numbered)[1])));
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    out.append(heading ? h("p", { class: "md-h" }, inline(heading[1])) : h("p", null, inline(line)));
  }
  return out;
}

function inline(text) {
  const parts = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    parts.push(token.startsWith("**") ? h("strong", null, token.slice(2, -2)) : h("code", null, token.slice(1, -1)));
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}
