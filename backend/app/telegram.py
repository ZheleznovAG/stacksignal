"""Minimal Telegram Bot API client: send a message, and discover which chat to send to.

The bot token is part of every request URL, so it must never reach a log line or an error
message: failures are reduced to short codes here and the original exception (whose text can
contain the URL) is deliberately not chained.
"""

import re

import httpx

from . import config

API = "https://api.telegram.org"


class TelegramError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def token_shape_ok() -> bool:
    """A BotFather token is "<number>:<35 url-safe characters>", e.g. 123456789:AAH... — people often
    paste only the part after the colon. Checked locally so the mistake can be explained."""
    return bool(re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", config.TELEGRAM_BOT_TOKEN))


def _client(transport=None) -> httpx.Client:
    # trust_env: honour HTTPS_PROXY if the user has one. An explicit TELEGRAM_PROXY wins.
    kwargs = {"timeout": 20, "trust_env": True}
    if transport is not None:
        kwargs["transport"] = transport
    elif config.TELEGRAM_PROXY:
        kwargs["proxy"] = config.TELEGRAM_PROXY
    return httpx.Client(**kwargs)


def _error_code(status: int, description: str) -> str:
    description = (description or "").lower()
    if status in (401, 404):  # Telegram answers 404 (not 401) for a malformed token
        return "telegram_bad_token"
    if status == 403:
        return "telegram_bot_blocked"  # the user has not pressed Start, or blocked the bot
    if status == 400 and "chat not found" in description:
        return "telegram_chat_not_found"
    if status == 429:
        return "telegram_rate_limited"
    return f"telegram_error_{status}"


def _call(method: str, payload: dict, transport=None):
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        raise TelegramError("telegram_not_configured")
    try:
        with _client(transport) as client:
            response = client.post(f"{API}/bot{token}/{method}", json=payload)
    except httpx.HTTPError:
        raise TelegramError("telegram_unreachable") from None
    except ImportError:  # a socks:// proxy without the optional socksio package
        raise TelegramError("proxy_support_missing") from None
    try:
        data = response.json()
    except ValueError:
        raise TelegramError("telegram_bad_response") from None
    if not isinstance(data, dict) or not data.get("ok"):
        description = data.get("description", "") if isinstance(data, dict) else ""
        raise TelegramError(_error_code(response.status_code, description))
    return data.get("result")


def send_message(text: str, *, transport=None) -> None:
    if not config.TELEGRAM_CHAT_ID:
        raise TelegramError("telegram_not_configured")
    _call("sendMessage", {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True}, transport)


def find_chats(*, transport=None) -> list[dict]:
    """Chats that recently wrote to the bot — how a user finds their TELEGRAM_CHAT_ID."""
    updates = _call("getUpdates", {"limit": 100, "timeout": 0}, transport) or []
    chats: dict[int, dict] = {}
    for update in updates:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if chat and "id" in chat:
                name = chat.get("title") or " ".join(
                    part for part in (chat.get("first_name"), chat.get("last_name")) if part
                ) or chat.get("username") or ""
                chats[chat["id"]] = {"id": chat["id"], "type": chat.get("type", ""), "name": name}
    return list(chats.values())
