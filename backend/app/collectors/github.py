"""GitHub collector.

GitHub has no "trending" API, so the global part approximates it with two repository searches:
  - "new & rising": created within the collection window, sorted by stars
  - "active & popular": pushed to within the last 7 days, sorted by stars, high star floor
Both exclude forks and archived repositories. On top of that:
  - search_repos(): a query written for one project ("soft body physics"), so niche projects get
    their own finds instead of only what is hot worldwide;
  - get_repo(): one repository by name, to refresh the numbers of items we already track.

Unauthenticated access is limited (search: 10 requests/min, other calls: 60/hour), so every
function raises RateLimited instead of hammering, and the pipeline stops using GitHub for the
run. A GITHUB_TOKEN (no scopes needed) raises the limits to 30/min and 5000/hour.
"""

import datetime
import time

import httpx

from .. import config
from ..config import COLLECT_WINDOW_DAYS, GITHUB_TOKEN

API_URL = "https://api.github.com/search/repositories"
REPO_URL = "https://api.github.com/repos/{}"
USER_URL = "https://api.github.com/users/{}"


class RateLimited(RuntimeError):
    """GitHub says slow down; further requests in this run would only fail."""


def _headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _get(client: httpx.Client, url: str, params: dict | None = None, accept: str | None = None):
    resp = client.get(url, params=params, headers=_headers(accept) if accept else _headers(), timeout=30)
    if resp.status_code in (403, 429) and (
        resp.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in resp.text.lower()
    ):
        raise RateLimited("GitHub rate limit reached")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub request failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


_last_search = 0.0


def _search(client: httpx.Client, query: str, per_page: int = 40, page: int = 1) -> list[dict]:
    """One page of search results, spaced from the previous search to stay under GitHub's search limit."""
    global _last_search
    wait = _last_search + config.GITHUB_SEARCH_PAUSE - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        data = _get(client, API_URL, {"q": query, "sort": "stars", "order": "desc", "per_page": per_page, "page": page})
    finally:
        _last_search = time.monotonic()
    return (data or {}).get("items", [])


def normalize(repo: dict) -> dict:
    """Pure transform from a raw GitHub search-API repo object to our normalized item
    shape. Kept separate from collect() so it can be unit tested without network access."""
    return {
        "source": "github",
        "external_id": repo["full_name"],
        "name": repo["name"],
        "owner": repo["owner"]["login"],
        "url": repo["html_url"],
        "description": repo.get("description") or "",
        "topics": repo.get("topics") or [],
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "downloads": 0,
        "likes": 0,
        "source_created_at": repo.get("created_at"),
        "source_updated_at": repo.get("pushed_at"),
        "raw": {
            "language": repo.get("language"),
            "license": (repo.get("license") or {}).get("spdx_id"),
            "open_issues": repo.get("open_issues_count"),
            "archived": repo.get("archived"),
        },
    }


def collect(window_days: int | None = None, *, transport=None) -> list[dict]:
    window_days = window_days or COLLECT_WINDOW_DAYS
    since = (datetime.date.today() - datetime.timedelta(days=window_days)).isoformat()
    recent_push = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    results: dict[str, dict] = {}
    with httpx.Client(transport=transport) as client:
        for repo in _search(client, f"created:>={since} stars:>=50 fork:false archived:false", 50):
            results[repo["full_name"]] = repo
        for repo in _search(client, f"pushed:>={recent_push} stars:>=500 fork:false archived:false", 40):
            results.setdefault(repo["full_name"], repo)
    return [normalize(repo) for repo in results.values()]


def _phrase(text: str) -> str:
    text = text.strip()
    return f'"{text}"' if " " in text and not text.startswith('"') else text


def search_repos(text: str, *, min_stars: int = 15, pushed_days: int = 120, per_page: int = 30,
                 pages: int | None = None, transport=None) -> list[dict]:
    """Recently active repositories matching `text` in name, description or topics, best first. Reads up to
    `pages` result pages (GITHUB_SEARCH_PAGES), stopping early once a page is not full."""
    pages = config.GITHUB_SEARCH_PAGES if pages is None else pages
    pushed = (datetime.date.today() - datetime.timedelta(days=pushed_days)).isoformat()
    query = (f"{_phrase(text)} in:name,description,topics stars:>={min_stars} pushed:>={pushed} "
             "fork:false archived:false")
    repos: list[dict] = []
    with httpx.Client(transport=transport) as client:
        for page in range(1, max(pages, 1) + 1):
            found = _search(client, query, per_page, page)
            repos.extend(found)
            if len(found) < per_page:
                break
    return [normalize(repo) for repo in repos]


def get_releases(full_name: str, *, limit: int = 5, transport=None) -> list[dict]:
    """The repository's newest published releases (drafts skipped), newest first; [] when it has none."""
    with httpx.Client(transport=transport) as client:
        data = _get(client, REPO_URL.format(full_name) + "/releases", {"per_page": limit})
    return [{"tag": r["tag_name"], "name": r.get("name") or r["tag_name"], "url": r.get("html_url") or "",
             "published_at": r.get("published_at") or r.get("created_at"), "prerelease": bool(r.get("prerelease")),
             "notes": (r.get("body") or "").strip()}
            for r in (data or []) if not r.get("draft") and r.get("tag_name")]


def get_repo(full_name: str, *, transport=None) -> dict | None:
    """One repository's current numbers, or None if it no longer exists."""
    with httpx.Client(transport=transport) as client:
        repo = _get(client, REPO_URL.format(full_name))
    return normalize(repo) if repo else None


def _owner(account: dict) -> dict:
    return {"login": account["login"], "id": account.get("id"),
            "kind": "organization" if account.get("type") == "Organization" else "user"}


def _pages(url: str, *, accept: str | None = None, max_pages: int | None = None, transport=None) -> list:
    """Every row of a paginated list endpoint, up to `max_pages` pages of 100."""
    max_pages = config.GITHUB_SYNC_MAX_PAGES if max_pages is None else max_pages
    rows: list = []
    with httpx.Client(transport=transport) as client:
        for page in range(1, max(max_pages, 1) + 1):
            found = _get(client, url, {"per_page": 100, "page": page}, accept=accept) or []
            rows.extend(found)
            if len(found) < 100:
                break
    return rows


def get_user(login: str, *, transport=None) -> dict | None:
    """A user or organization account ({login, id, kind}), or None if there is no such account."""
    with httpx.Client(transport=transport) as client:
        account = _get(client, USER_URL.format(login))
    return _owner(account) if account else None


def user_following(login: str, *, transport=None) -> list[dict]:
    """The accounts `login` follows, as {login, id, kind}."""
    return [_owner(account) for account in _pages(USER_URL.format(login) + "/following", transport=transport)]


def user_starred(login: str, *, transport=None) -> list[dict]:
    """Repositories `login` starred, newest star first: {"starred_at", "owner": {login, id, kind}, "payload"}.
    The payload is the normalized item; the star media type adds when each star was given."""
    rows = _pages(USER_URL.format(login) + "/starred", accept="application/vnd.github.star+json", transport=transport)
    return [{"starred_at": row.get("starred_at"), "owner": _owner(row["repo"]["owner"]), "payload": normalize(row["repo"])}
            for row in rows if isinstance(row, dict) and isinstance(row.get("repo"), dict)]


def user_repos(login: str, *, transport=None) -> list[dict]:
    """The account's own public repositories, most recently pushed first (one page of 100):
    {"payload", "owner", "fork", "archived", "created_at", "pushed_at"}."""
    with httpx.Client(transport=transport) as client:
        rows = _get(client, USER_URL.format(login) + "/repos", {"sort": "pushed", "per_page": 100}) or []
    return [{"payload": normalize(repo), "owner": _owner(repo["owner"]), "fork": bool(repo.get("fork")),
             "archived": bool(repo.get("archived")), "created_at": repo.get("created_at"),
             "pushed_at": repo.get("pushed_at")}
            for repo in rows if isinstance(repo, dict) and isinstance(repo.get("owner"), dict)]
