from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import config, telegram
from .. import digest as digest_module
from ..db import get_db
from ..scheduler import next_run

router = APIRouter()


def _digest_http_error(error: telegram.TelegramError) -> HTTPException:
    return HTTPException(400 if error.code == "telegram_not_configured" else 502, error.code)


@router.get("/api/digest/status")
def digest_status(db: Session = Depends(get_db)):
    return {
        "configured": telegram.configured(), "token_set": bool(config.TELEGRAM_BOT_TOKEN),
        "chat_id_set": bool(config.TELEGRAM_CHAT_ID), "proxy_set": bool(config.TELEGRAM_PROXY),
        "token_shape_ok": telegram.token_shape_ok(),
        "enabled": config.DIGEST_ENABLED, "lang": config.DIGEST_LANG,
        "min_relevance": config.DIGEST_MIN_RELEVANCE, "max_items": config.DIGEST_MAX_ITEMS,
        "daily_run_time": config.DAILY_RUN_TIME, "next_run": next_run(),
        "recent": digest_module.recent_logs(db),
    }


@router.get("/api/digest/preview")
def digest_preview(db: Session = Depends(get_db)):
    """What would be sent right now, without sending or recording anything."""
    built = digest_module.build_digest(db)
    return {"empty": built.empty, "items": len(built.items), "opportunities": len(built.opportunities),
            "messages": [digest_module.plain(message) for message in built.messages]}


@router.post("/api/digest/send")
def digest_send(db: Session = Depends(get_db)):
    try:
        return digest_module.send_digest(db, trigger="manual", force=True)
    except telegram.TelegramError as error:
        raise _digest_http_error(error)


@router.post("/api/digest/chats")
def digest_chats():
    """Chats that wrote to the bot recently — how to find your TELEGRAM_CHAT_ID."""
    try:
        return telegram.find_chats()
    except telegram.TelegramError as error:
        raise _digest_http_error(error)
