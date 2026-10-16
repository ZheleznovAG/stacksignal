import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "stacksignal.db"
# Overridable so the browser tests (backend/e2e/) run the real server on a scratch database.
DATABASE_URL = os.getenv("STACKSIGNAL_DATABASE_URL", "").strip() or f"sqlite:///{DB_PATH}"

def _shared_gateway_key() -> str:
    key = os.getenv("GATEWAY_API_KEY", "")
    if key or os.name != "nt":
        return key
    # The sibling gateway launcher stores its local service key in user environment,
    # outside either repository. Existing terminal sessions can read it immediately.
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment:
            return winreg.QueryValueEx(environment, "GATEWAY_API_KEY")[0]
    except OSError:
        return ""


AI_BASE_URL = os.getenv("AI_BASE_URL", "http://127.0.0.1:8787").rstrip("/")
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
if not AI_API_KEY and AI_BASE_URL == "http://127.0.0.1:8787":
    AI_API_KEY = _shared_gateway_key().strip()
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT", "330"))
AI_REQUEST_TIMEOUT_MS = int(os.getenv("AI_REQUEST_TIMEOUT_MS", "300000"))
AI_MAX_CALLS_PER_RUN = int(os.getenv("AI_MAX_CALLS_PER_RUN", "6"))
# Items analysed per gateway call. One call costs ~15-25s of browser time regardless of
# how many items it carries, so batching is what raises coverage per run.
AI_BATCH_SIZE = int(os.getenv("AI_BATCH_SIZE", "5"))
AI_RELEVANCE_BATCH_SIZE = int(os.getenv("AI_RELEVANCE_BATCH_SIZE", "10"))  # relevance-only calls are short
if AI_TIMEOUT <= 0 or AI_REQUEST_TIMEOUT_MS < 1000 or AI_MAX_CALLS_PER_RUN < 0:
    raise ValueError("AI timeouts must be positive and AI_MAX_CALLS_PER_RUN nonnegative")
if not 1 <= AI_BATCH_SIZE <= 20:
    raise ValueError("AI_BATCH_SIZE must be between 1 and 20")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
# Your GitHub account: its stars and the people it follows feed discovery (app/authors.py).
# Public endpoints; empty disables the sync.
GITHUB_LOGIN = os.getenv("GITHUB_LOGIN", "").strip()
GITHUB_SYNC_MAX_PAGES = int(os.getenv("GITHUB_SYNC_MAX_PAGES", "10"))  # 100 stars / follows per page
# Watched authors (app/authors.py): how many accounts are checked per run, how often, and how many of
# their repositories may enter the radar per run (each one is a candidate for an AI read).
AUTHOR_CHECKS_PER_RUN = int(os.getenv("AUTHOR_CHECKS_PER_RUN", "25"))
AUTHOR_CHECK_DAYS = int(os.getenv("AUTHOR_CHECK_DAYS", "7"))              # watched authors
AUTHOR_CANDIDATE_CHECK_DAYS = int(os.getenv("AUTHOR_CANDIDATE_CHECK_DAYS", "30"))  # suggested by your finds
AUTHOR_PROMOTE_PER_RUN = int(os.getenv("AUTHOR_PROMOTE_PER_RUN", "10"))
AUTHOR_PROMOTE_PER_AUTHOR = int(os.getenv("AUTHOR_PROMOTE_PER_AUTHOR", "3"))
AUTHOR_MIN_STARS = int(os.getenv("AUTHOR_MIN_STARS", "25"))   # an older repository must have earned this many
AUTHOR_NEW_DAYS = int(os.getenv("AUTHOR_NEW_DAYS", "120"))     # younger counts as a new repository
AUTHOR_NEW_ORG_MIN_STARS = int(os.getenv("AUTHOR_NEW_ORG_MIN_STARS", "10"))  # organizations publish a lot
COLLECT_WINDOW_DAYS = int(os.getenv("COLLECT_WINDOW_DAYS", "30"))

LLM_ENABLED = bool(AI_API_KEY) and os.getenv("AI_ENABLED", "0") == "1"

# Local sentence embeddings for semantic chat retrieval (app/embeddings.py). Unlike AI_ENABLED
# this needs no key -- the model is a one-time download -- so it defaults on; tests force it off
# the same way they force AI_ENABLED off, so the suite stays network-free.
EMBEDDINGS_ENABLED = os.getenv("EMBEDDINGS_ENABLED", "1") != "0"

# --- Daily schedule and the Telegram digest -------------------------------------------------
def _clock(value: str) -> tuple[int, int]:
    try:
        hour, minute = (int(part) for part in value.strip().split(":"))
    except ValueError:
        raise ValueError("DAILY_RUN_TIME must look like 08:00") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("DAILY_RUN_TIME must be a valid 24-hour time")
    return hour, minute


SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "1") != "0"  # 0: no automatic daily run (browser tests)
DAILY_RUN_TIME = os.getenv("DAILY_RUN_TIME", "08:00").strip()  # local time of the automatic run
DAILY_RUN_HOUR, DAILY_RUN_MINUTE = _clock(DAILY_RUN_TIME)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip()  # e.g. socks5://127.0.0.1:10808
DIGEST_ENABLED = os.getenv("DIGEST_ENABLED", "1") != "0"
DIGEST_LANG = os.getenv("DIGEST_LANG", "ru").strip().lower()
# Language of the AI-written reading texts (summaries, reasons, briefings). Identifiers stay English.
AI_TEXT_LANG = os.getenv("AI_TEXT_LANG", "ru").strip().lower()
DIGEST_MIN_RELEVANCE = float(os.getenv("DIGEST_MIN_RELEVANCE", "0.6"))
DIGEST_MAX_ITEMS = int(os.getenv("DIGEST_MAX_ITEMS", "8"))
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8000").strip()
# Host names the dashboard answers to (see app/security.py). Anything else -- e.g. a DNS-rebinding
# domain pointed at 127.0.0.1 -- is refused. Add a name here only together with real authentication.
ALLOWED_HOSTS = {h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,::1").split(",") if h.strip()}
if DIGEST_LANG not in ("ru", "en") or AI_TEXT_LANG not in ("ru", "en"):
    raise ValueError("DIGEST_LANG and AI_TEXT_LANG must be ru or en")
if not 0 <= DIGEST_MIN_RELEVANCE <= 1 or not 1 <= DIGEST_MAX_ITEMS <= 30:
    raise ValueError("DIGEST_MIN_RELEVANCE must be 0..1 and DIGEST_MAX_ITEMS 1..30")


# --- Discovery: how much a run may ask of GitHub / Hugging Face ------------------------------
# Unauthenticated GitHub allows 10 searches/min and 60 other calls/hour, so the defaults are small
# without a GITHUB_TOKEN. Queries and tracked items rotate stalest-first, so a small budget still
# covers everything over a few days.
DISCOVERY_ENABLED = os.getenv("DISCOVERY_ENABLED", "1") != "0"
GITHUB_QUERIES_PER_RUN = int(os.getenv("GITHUB_QUERIES_PER_RUN", "24" if GITHUB_TOKEN else "8"))
# Result pages read per GitHub search (30 repositories each, best by stars first). More pages reach
# deeper into a crowded topic. GitHub allows 30 searches a minute with a token and 10 without,
# so searches are spaced out (seconds between two searches).
GITHUB_SEARCH_PAGES = int(os.getenv("GITHUB_SEARCH_PAGES", "3" if GITHUB_TOKEN else "1"))
GITHUB_SEARCH_PAUSE = float(os.getenv("GITHUB_SEARCH_PAUSE", "2.2" if GITHUB_TOKEN else "6.5"))
HF_QUERIES_PER_RUN = int(os.getenv("HF_QUERIES_PER_RUN", "24"))
# More sources, searched only by queries written for your projects (no global lists for them).
ARXIV_QUERIES_PER_RUN = int(os.getenv("ARXIV_QUERIES_PER_RUN", "12"))
ARXIV_DAYS = int(os.getenv("ARXIV_DAYS", "120"))                 # only papers submitted this recently
ARXIV_PAUSE = float(os.getenv("ARXIV_PAUSE", "3.1"))             # arXiv asks for one request per 3 seconds
NPM_QUERIES_PER_RUN = int(os.getenv("NPM_QUERIES_PER_RUN", "8"))
REFRESH_NPM_PER_RUN = int(os.getenv("REFRESH_NPM_PER_RUN", "20"))
REFRESH_PYPI_PER_RUN = int(os.getenv("REFRESH_PYPI_PER_RUN", "20"))
REFRESH_RELEASES_PER_RUN = int(os.getenv("REFRESH_RELEASES_PER_RUN", "60" if GITHUB_TOKEN else "10"))  # repos checked for new releases
STACK_WATCH_PER_RUN = int(os.getenv("STACK_WATCH_PER_RUN", "60"))  # dependencies of your projects looked up per run
REFRESH_GITHUB_PER_RUN = int(os.getenv("REFRESH_GITHUB_PER_RUN", "100" if GITHUB_TOKEN else "15"))
REFRESH_HF_PER_RUN = int(os.getenv("REFRESH_HF_PER_RUN", "60"))
# Catalogues at least this big pick chat candidates through the full-text index; smaller ones score every item.
CHAT_FTS_MIN_ITEMS = int(os.getenv("CHAT_FTS_MIN_ITEMS", "300"))
STALE_DAYS = int(os.getenv("STALE_DAYS", "30"))  # untouched items unseen this long are archived
