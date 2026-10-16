"""Hugging Face collector: models via the public Hub API.

The global part reads two "what is hot" lists (likes over 7 days, trending score). The list of
the newest models was dropped: it brought in ~30 brand-new models per run regardless of quality
and was the main source of noise. A model that deserves attention shows up in the trending lists
within days. On top of that:
  - search_models(): a query written for one project, best by recent downloads first;
  - get_model(): one model by id, to refresh the numbers of items we already track.
Only models are collected — not datasets or Spaces.
"""

import datetime

import httpx

MODELS_URL = "https://huggingface.co/api/models"


def _fetch(client: httpx.Client, sort: str, limit: int = 50, search: str | None = None) -> list[dict]:
    params = {"sort": sort, "direction": -1, "limit": limit, "full": "true"}
    if search:
        params["search"] = search
    resp = client.get(MODELS_URL, params=params, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Hugging Face fetch failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def normalize(m: dict) -> dict:
    """Pure transform from a raw Hugging Face Hub API model object to our normalized
    item shape. Kept separate from collect() so it can be unit tested without network
    access. Hugging Face has no "stars"; likes + downloads stand in for GitHub's stars/
    forks pair in the scoring code."""
    return {
        "source": "huggingface",
        "external_id": m["id"],
        "name": m["id"].split("/")[-1],
        "owner": m["id"].split("/")[0] if "/" in m["id"] else "",
        "url": f"https://huggingface.co/{m['id']}",
        "description": ", ".join(m.get("tags", [])[:8]),
        "topics": m.get("tags") or [],
        "stars": 0,
        "forks": 0,
        "downloads": m.get("downloads", 0) or 0,
        "likes": m.get("likes", 0) or 0,
        "source_created_at": m.get("createdAt"),
        "source_updated_at": m.get("lastModified"),
        "raw": {
            "pipeline_tag": m.get("pipeline_tag"),
            "library_name": m.get("library_name"),
        },
    }


def collect(window_days: int | None = None, *, transport=None) -> list[dict]:
    """The global "what is hot" lists. `window_days` is accepted for interface parity with the
    GitHub collector; trending lists have no window."""
    results: dict[str, dict] = {}
    with httpx.Client(transport=transport) as client:
        for sort in ("likes7d", "trendingScore"):
            try:
                for m in _fetch(client, sort):
                    results.setdefault(m["id"], m)
            except RuntimeError:
                continue
    return [normalize(m) for m in results.values()]


def search_models(text: str, *, sort: str = "downloads", limit: int = 30, transport=None) -> list[dict]:
    with httpx.Client(transport=transport) as client:
        return [normalize(m) for m in _fetch(client, sort, limit, search=text.strip())]


def get_model(model_id: str, *, transport=None) -> dict | None:
    """One model's current numbers, or None if it no longer exists."""
    with httpx.Client(transport=transport) as client:
        resp = client.get(f"{MODELS_URL}/{model_id}", timeout=30)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"Hugging Face fetch failed ({resp.status_code})")
    return normalize(resp.json())


def _parse(ts: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None
