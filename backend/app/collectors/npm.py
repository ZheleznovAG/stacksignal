"""npm collector: packages found by a query written for one of your projects, and one package by name.

The registry's search returns monthly downloads with every hit, which is the traction signal here. There
is no global "hot" list: packages come from project searches, and from refreshing ones already tracked.
"""

from urllib.parse import quote

import httpx

SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
PACKAGE_URL = "https://registry.npmjs.org/{}/latest"
DOWNLOADS_URL = "https://api.npmjs.org/downloads/point/last-month/{}"
_HEADERS = {"User-Agent": "StackSignal/1.0 (personal technology radar)"}


def _encode(name: str) -> str:
    return quote(name, safe="@")  # scoped names keep their "@" and get "%2F" for the slash


def normalize(hit: dict) -> dict:
    """Pure transform from one registry search hit ({"package": ..., "downloads": ...}) to our item shape."""
    package = hit["package"]
    name = package["name"]
    publisher = (package.get("publisher") or {}).get("username") or ""
    return {
        "source": "npm",
        "external_id": name,
        "name": name,
        "owner": publisher or (name.split("/")[0].lstrip("@") if name.startswith("@") else ""),
        "url": (package.get("links") or {}).get("npm") or f"https://www.npmjs.com/package/{name}",
        "description": package.get("description") or "",
        "topics": list(package.get("keywords") or [])[:8],
        "stars": 0, "forks": 0,
        "downloads": int((hit.get("downloads") or {}).get("monthly") or 0),  # last 30 days
        "likes": 0,
        "source_created_at": None,  # the search does not say when the package first appeared
        "source_updated_at": package.get("date"),
        "raw": {"version": package.get("version"), "license": package.get("license") if isinstance(package.get("license"), str) else None,
                "homepage": (package.get("links") or {}).get("homepage"),
                "repository": (package.get("links") or {}).get("repository"),
                "score": ((hit.get("score") or {}).get("detail") or {})},
    }


def search_packages(text: str, *, size: int = 20, transport=None) -> list[dict]:
    with httpx.Client(transport=transport, headers=_HEADERS, timeout=30) as client:
        resp = client.get(SEARCH_URL, params={"text": text, "size": size})
    if resp.status_code != 200:
        raise RuntimeError(f"npm search failed ({resp.status_code})")
    return [normalize(hit) for hit in resp.json().get("objects", [])]


def get_package(name: str, *, transport=None) -> dict | None:
    """One package's latest version and last-month downloads, or None if it does not exist."""
    with httpx.Client(transport=transport, headers=_HEADERS, timeout=30) as client:
        resp = client.get(PACKAGE_URL.format(_encode(name)))
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(f"npm fetch failed ({resp.status_code})")
        latest = resp.json()
        downloads = client.get(DOWNLOADS_URL.format(_encode(name)))
    monthly = downloads.json().get("downloads", 0) if downloads.status_code == 200 else 0
    repository = latest.get("repository")
    repository_url = repository.get("url") if isinstance(repository, dict) else repository
    hit = {"package": {**latest, "links": {"npm": f"https://www.npmjs.com/package/{name}",
                                           "homepage": latest.get("homepage"), "repository": repository_url},
                       "publisher": {"username": (latest.get("_npmUser") or {}).get("name", "")}},
           "downloads": {"monthly": monthly}}
    return normalize(hit)
