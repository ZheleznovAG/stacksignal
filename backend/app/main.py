import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, context_profiles, needs as needs_module
from .db import init_db
from .maintenance import apply_pending
from .routers import (authors, capabilities, chat, collect, digest, discover, health, items, opportunities,
                      projects, queries, radar, relevant, today, needs, experiments)
from .scheduler import start_scheduler, stop_scheduler
from .security import LocalOnlyMiddleware
from .seed import apply_project_profiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
# httpx logs every request URL at INFO, and a Telegram bot token is part of its URL path.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .db import SessionLocal

    db = SessionLocal()
    try:
        apply_project_profiles(db)
        context_profiles.apply(db)
        needs_module.import_missing(db)
        apply_pending(db)
    finally:
        db.close()
    if config.SCHEDULER_ENABLED:
        start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="StackSignal", lifespan=lifespan)
# No CORS middleware: the frontend is served from this same FastAPI process/origin, so
# no cross-origin access is needed. A wildcard CORS policy here would let any website
# a browser has open make authenticated-by-network-position requests against this API
# (there's no per-request auth — see README's Security section).
app.add_middleware(LocalOnlyMiddleware, allowed_hosts=config.ALLOWED_HOSTS)

for router_module in (health, today, projects, queries, capabilities, items, discover, relevant, radar,
                      opportunities, digest, collect, chat, authors, needs, experiments):
    app.include_router(router_module.router)


# ---------- static frontend ----------

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
# The dashboard is native ES modules, and browsers refuse a module served as anything but JavaScript.
# On Windows, mimetypes reads the registry, where .js is sometimes registered as text/plain.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
if FRONTEND_DIR.exists():
    class DashboardFiles(StaticFiles):
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            # ES modules keep stable URLs: revalidate them after local updates so
            # browsers cannot combine old and new interface files.
            response.headers["Cache-Control"] = "no-cache"
            return response

    app.mount("/", DashboardFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
