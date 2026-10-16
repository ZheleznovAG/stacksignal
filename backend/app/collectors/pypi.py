"""PyPI collector: one package by name.

PyPI has no search API, so packages get here by Add URL and by watching the dependencies of your
projects (their newest version and upload date), not by discovery.
"""

import httpx

PACKAGE_URL = "https://pypi.org/pypi/{}/json"
_HEADERS = {"User-Agent": "StackSignal/1.0 (personal technology radar)"}


def normalize(data: dict) -> dict:
    info = data["info"]
    uploads = sorted(f["upload_time_iso_8601"] for files in (data.get("releases") or {}).values() for f in files
                     if f.get("upload_time_iso_8601"))
    latest_uploads = sorted(f["upload_time_iso_8601"] for f in (data.get("urls") or []) if f.get("upload_time_iso_8601"))
    urls = info.get("project_urls") or {}
    keywords = [k.strip() for k in (info.get("keywords") or "").replace(";", ",").split(",") if k.strip()]
    topics = keywords + [c.split(" :: ")[-1] for c in info.get("classifiers") or [] if c.startswith("Topic ::")]
    return {
        "source": "pypi",
        "external_id": info["name"],
        "name": info["name"],
        "owner": info.get("author") or "",
        "url": f"https://pypi.org/project/{info['name']}/",
        "description": (info.get("summary") or "").strip(),
        "topics": list(dict.fromkeys(topics))[:8],
        "stars": 0, "forks": 0, "downloads": 0, "likes": 0,  # PyPI itself publishes no download counts
        "source_created_at": uploads[0] if uploads else None,
        "source_updated_at": latest_uploads[-1] if latest_uploads else (uploads[-1] if uploads else None),
        "raw": {"version": info.get("version"), "license": info.get("license_expression") or info.get("license"),
                "requires_python": info.get("requires_python"),
                "repository": urls.get("Repository") or urls.get("Source") or urls.get("Homepage"),
                "changelog": urls.get("Changelog")},
    }


def get_package(name: str, *, transport=None) -> dict | None:
    """One package's newest version and dates, or None if it does not exist."""
    with httpx.Client(transport=transport, headers=_HEADERS, timeout=30, follow_redirects=True) as client:
        resp = client.get(PACKAGE_URL.format(name))
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"PyPI fetch failed ({resp.status_code})")
    return normalize(resp.json())
