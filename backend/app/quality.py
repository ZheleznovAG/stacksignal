"""The quality gate: what is worth keeping from a collection, and what to archive later.

Broad "what is hot" lists are noisy (86% of the Hugging Face intake had almost no likes or
downloads), so every candidate must show some traction and some substance. Items found by a
search for one of *your* projects get a lower bar: a niche 20-star repository for soft-body
physics is exactly what you want to see, even though it would be noise in a global list.
"""

import re

# Traction: stars for GitHub; likes or (30-day) downloads for Hugging Face.
GITHUB_MIN_STARS = 50
GITHUB_MIN_STARS_TARGETED = 15
HF_MIN_LIKES = 10
HF_MIN_DOWNLOADS = 2000
HF_MIN_LIKES_TARGETED = 3
HF_MIN_DOWNLOADS_TARGETED = 300
# npm: monthly downloads. arXiv papers have no popularity, only an abstract worth reading.
NPM_MIN_DOWNLOADS = 3000
NPM_MIN_DOWNLOADS_TARGETED = 300
ARXIV_MIN_ABSTRACT = 80

# Names that are experiments, not products: "test", "tmp", "…-seed-42", "…-ratio-0.60-…".
_JUNK_NAME = re.compile(
    r"(^|[-_.])(test|tmp|temp|dummy|debug|sandbox|untitled)([-_.\d]|$)|seed[-_]?\d+|ratio[-_]\d",
    re.IGNORECASE,
)


def gate(payload: dict, *, targeted: bool = False) -> tuple[bool, str]:
    """(passes, reason) for a normalized collector payload."""
    raw = payload.get("raw") or {}
    if payload["source"] == "github":
        if raw.get("archived"):
            return False, "archived_repo"
        minimum = GITHUB_MIN_STARS_TARGETED if targeted else GITHUB_MIN_STARS
        if payload.get("stars", 0) < minimum:
            return False, "few_stars"
        if not (payload.get("description") or "").strip() and not payload.get("topics"):
            return False, "no_description"
        return True, ""

    if payload["source"] == "arxiv":
        if len((payload.get("description") or "").strip()) < ARXIV_MIN_ABSTRACT:
            return False, "no_abstract"
        return True, ""

    if payload["source"] == "pypi":
        return True, ""  # only ever added by hand or by watching a dependency

    if _JUNK_NAME.search(payload.get("name", "")):
        return False, "junk_name"

    if payload["source"] == "npm":
        minimum = NPM_MIN_DOWNLOADS_TARGETED if targeted else NPM_MIN_DOWNLOADS
        if payload.get("downloads", 0) < minimum:
            return False, "little_traction"
        if not (payload.get("description") or "").strip() and not payload.get("topics"):
            return False, "no_description"
        return True, ""

    likes, downloads = payload.get("likes", 0), payload.get("downloads", 0)
    min_likes = HF_MIN_LIKES_TARGETED if targeted else HF_MIN_LIKES
    min_downloads = HF_MIN_DOWNLOADS_TARGETED if targeted else HF_MIN_DOWNLOADS
    if likes < min_likes and downloads < min_downloads:
        return False, "little_traction"
    if not (raw.get("pipeline_tag") or raw.get("library_name") or len(payload.get("topics") or []) >= 2):
        return False, "no_metadata"
    return True, ""


def item_payload(item) -> dict:
    """An Item row shaped like a collector payload, so the same gate can judge stored items."""
    return {
        "source": getattr(item.source, "value", item.source), "name": item.name, "stars": item.stars,
        "likes": item.likes, "downloads": item.downloads, "description": item.description,
        "topics": item.topics or [], "raw": item.raw or {},
    }


AI_KEEP_MIN_RELEVANCE = 0.6  # what the AI must find for a project before its reading keeps an item alive


def ai_keeps(item, ai_analyzed: bool) -> bool:
    """The AI read the item AND found it strongly relevant to some project. Being read alone is not enough:
    otherwise every item the model ever looked at would stay in the catalogue forever."""
    return ai_analyzed and max((r.score for r in item.relevances), default=0.0) >= AI_KEEP_MIN_RELEVANCE


def has_engagement(item, ai_analyzed: bool) -> bool:
    """True when the user (or the AI, see ai_keeps) has already done something with the item — such items are
    never archived automatically. `ai_analyzed` means "the AI read it"; only strongly relevant ones count."""
    return (getattr(item.status, "value", item.status) != "new" or bool(item.feedback)
            or bool(getattr(item, "decisions", None)) or ai_keeps(item, ai_analyzed))
