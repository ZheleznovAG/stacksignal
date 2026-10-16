// Every user-visible string in one place (Russian), plus the formatters that read naturally in it.

export const T = {
  appTag: "радар технологий",
  nav: { today: "Сегодня", projects: "Проекты", explore: "Обзор", chat: "Чат", settings: "Настройки" },

  status: { new: "Новое", watch: "Наблюдаю", assess: "Оценить", trial: "Пробую", adopt: "Использую", hold: "Пауза", ignore: "Не нужно" },
  statusHint: {
    new: "ещё не разобрано", watch: "держу в поле зрения", assess: "стоит присмотреться", trial: "пробую в проекте",
    adopt: "использую", hold: "не сейчас, вернуться позже", ignore: "не для меня",
  },
  decision: { assess: "Оценить", trial: "Пробую", adopt: "Использую", watch: "Наблюдаю", hold: "Пауза", ignore: "Отклонено" },
  decisionHint: {
    new: "релевантно, решения ещё нет", assess: "стоит присмотреться", trial: "пробую здесь", adopt: "используется в проекте",
    watch: "держу в поле зрения", hold: "не сейчас, вернуться позже", ignore: "отклонено для этого проекта",
  },
  source: { github: "GitHub", huggingface: "Hugging Face", arxiv: "arXiv", npm: "npm", pypi: "PyPI" },
  tier: { active: "Активный", watch: "Наблюдение", dormant: "Спящий" },
  contextKind: { project: "Проект", lab: "Лаборатория", infrastructure: "Инфраструктура", experiment: "Эксперимент",
    goal: "Цель", personal_system: "Личная система", reference_project: "Референсный проект" },
  lifecycle: { active: "Активный", watch: "Наблюдение", incubate: "Идея в развитии", dormant: "Спящий", archived: "Архив" },
  lifecycleHint: { active: "До 3 находок в дайджесте.", watch: "До 1 находки в дайджесте.",
    incubate: "До 1 находки в дайджесте; поиск включается отдельно.", dormant: "Без дайджеста; история сохранена.",
    archived: "История сохранена; новые поиски и рекомендации выключены." },
  tierHint: {
    active: "ищем, до 3 элементов в дайджесте",
    watch: "ищем, до 1 элемента в дайджесте",
    dormant: "хранится, но без поиска и дайджеста",
  },
  stage: {
    collect: "Мировые списки", discovery: "Поиск по проектам", gate_and_score: "Фильтр и оценка",
    entity_linking: "Связывание", enrich: "AI-анализ", opportunities: "Брифинги", capabilities: "Способности",
    embeddings: "Эмбеддинги", housekeeping: "Уборка",
  },
  runStatus: { running: "идёт", completed: "успешно", partial: "частично", failed: "ошибка" },

  momentum: "Рост",
  momentumHint: "Как быстро растёт внимание прямо сейчас (не общая популярность)",
  maturity: "Зрелость",
  maturityHint: "Насколько безопасно на это опираться: возраст, лицензия, активность, распространённость",
  ai: "AI",
  aiHint: "Прочитано и оценено AI",
  keywords: "по словам",
  keywordsHint: "Оценено только по совпадению ключевых слов, AI ещё не читал",
  fresh: "новое",

  errors: {
    network: "Сервер не отвечает. Запущен ли StackSignal?",
    generic: "Что-то пошло не так",
    invalid_url: "Это не похоже на ссылку на репозиторий, модель, статью или пакет.",
    unsupported_url: "Поддерживаются репозитории GitHub, модели Hugging Face, статьи arXiv, пакеты npm и PyPI.",
    not_found: "По этой ссылке ничего не нашлось.",
    fetch_failed: "Сайт сейчас недоступен (лимит или сеть). Попробуйте через минуту.",
    not_a_folder: "Такой папки на этом компьютере нет.",
    folder_unreadable: "Не удалось прочитать папку. Проверьте доступ к ней.",
    not_a_project_subfolder: "Проект должен находиться непосредственно в выбранной общей папке. Запустите сканирование заново.",
    collection_already_running: "Сбор уже идёт — дождитесь его окончания.",
    telegram_not_configured: "Telegram не настроен: добавьте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env и перезапустите.",
    telegram_bad_token: "Telegram отверг токен. Нужен целиком, из BotFather: число, двоеточие, длинная часть.",
    telegram_chat_not_found: "Чат не найден: откройте бота в Telegram, нажмите Start, проверьте TELEGRAM_CHAT_ID.",
    telegram_bot_blocked: "Бот не может вам написать — откройте его в Telegram и нажмите Start.",
    telegram_unreachable: "Telegram недоступен с этого компьютера. Если он заблокирован, задайте TELEGRAM_PROXY в .env.",
    telegram_rate_limited: "Telegram просит подождать — попробуйте через минуту.",
    proxy_support_missing: "Для TELEGRAM_PROXY нужен пакет socks: pip install \"httpx[socks]\".",
  },
  gatewayCode: {
    dispatch_paused: "анализ приостановлен после ошибки; требуется проверка шлюза",
    local_rate_limited: "ожидание общего бюджета или интервала шлюза",
    run_budget_exhausted: "бюджет этого запуска исчерпан",
    ai_disabled: "AI выключен",
    provider_unavailable: "браузер AI недоступен",
    response_timeout: "ответ не получен; повторная отправка заблокирована",
    ready: "готов",
    gateway_unavailable: "шлюз не отвечает",
    deadline_exceeded: "завис по таймауту",
    chat_location_unavailable: "не открывается проект ChatGPT",
    provider_recovery_required: "нужен перезапуск",
    authentication_required: "нужно войти в ChatGPT",
    starting: "запускается",
  },
};

export function errorText(err) {
  if (!err) return T.errors.generic;
  if (err.network) return T.errors.network;
  return T.errors[err.detail] || err.detail || err.message || T.errors.generic;
}

export function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export const count = (n, one, few, many) => `${fmtNum(n)} ${plural(n, one, few, many)}`;

// The server sends naive UTC timestamps.
export function parseTime(iso) {
  if (!iso) return null;
  return new Date(/(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : iso + "Z");
}

export function timeAgo(iso) {
  const date = parseTime(iso);
  if (!date) return "";
  const s = (Date.now() - date.getTime()) / 1000;
  if (s < 60) return "только что";
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  const days = Math.round(s / 86400);
  if (days < 30) return `${days} ${plural(days, "день", "дня", "дней")} назад`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months} мес назад`;
  return `${Math.round(days / 365)} г назад`;
}

export function isFresh(iso, days = 3) {
  const date = parseTime(iso);
  return date && Date.now() - date.getTime() < days * 86400000;
}

const compact = new Intl.NumberFormat("ru", { notation: "compact", maximumFractionDigits: 1 });
const plain = new Intl.NumberFormat("ru");
export function fmtNum(n) {
  n = Number(n) || 0;
  return Math.abs(n) >= 10000 ? compact.format(n) : plain.format(n);
}

export const pct = (x) => `${Math.round((Number(x) || 0) * 100)}%`;

const dateTime = new Intl.DateTimeFormat("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const dateLong = new Intl.DateTimeFormat("ru", { weekday: "long", day: "numeric", month: "long" });
export const fmtDateTime = (iso) => (parseTime(iso) ? dateTime.format(parseTime(iso)) : "");
export const fmtToday = () => dateLong.format(new Date());

export function duration(seconds) {
  const s = Number(seconds) || 0;
  if (s < 1) return `${s.toFixed(1).replace(".", ",")} с`;
  if (s < 60) return `${Math.round(s)} с`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s % 60);
  return rest ? `${m} мин ${rest} с` : `${m} мин`;
}
