import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone

from . import config, digest, telegram
from .db import SessionLocal
from .pipeline import CollectionBusy, run_collection

logger = logging.getLogger("stacksignal.scheduler")

JOB_ID = "daily_collection"
_scheduler: BackgroundScheduler | None = None


def _send_digest() -> None:
    if not (config.DIGEST_ENABLED and telegram.configured()):
        return
    db = SessionLocal()
    try:
        logger.info("Telegram digest: %s", digest.send_digest(db, trigger="scheduled"))
    except telegram.TelegramError as error:
        logger.warning("Telegram digest not sent: %s", error.code)  # a code only, never the token
    except Exception:
        logger.exception("Telegram digest failed")
    finally:
        db.close()


def _job() -> None:
    db = SessionLocal()
    sendable = False
    try:
        logger.info("Running scheduled collection")
        sendable = run_collection(db).status in ("completed", "partial")  # a source failing is fine, a crash is not
    except CollectionBusy:
        logger.info("Scheduled collection skipped: another run is in progress")
    except Exception:
        logger.exception("Scheduled collection failed")
    finally:
        db.close()
    if sendable:
        _send_digest()  # after the run, so the digest carries what this run just found
    else:
        logger.warning("No digest: the collection did not finish")


def add_jobs(scheduler: BackgroundScheduler) -> None:
    """One job: collect every day at DAILY_RUN_TIME (local time), then send the digest.
    coalesce + a long grace period mean a run delayed by a sleeping computer still happens."""
    scheduler.add_job(
        _job,
        CronTrigger(hour=config.DAILY_RUN_HOUR, minute=config.DAILY_RUN_MINUTE, timezone=scheduler.timezone),
        id=JOB_ID, replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=6 * 3600,
    )


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone=get_localzone())
    add_jobs(_scheduler)
    _scheduler.start()
    return _scheduler


def next_run() -> str | None:
    job = _scheduler.get_job(JOB_ID) if _scheduler is not None else None
    return job.next_run_time.isoformat() if job is not None and job.next_run_time else None


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
