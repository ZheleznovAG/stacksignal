"""arXiv collector: recent papers matching a query written for one of your projects.

There is no "what is hot" list here — arXiv has no popularity signal, and a global feed of new papers
is exactly the noise this radar tries to avoid. Papers come only from project searches, so each one
is in the database because of the project it might matter to, and the AI reads the abstract and
decides how much. arXiv asks for at most one request per three seconds; searches are spaced
accordingly and a transient refusal is retried after a pause.
"""

import datetime
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import httpx

from .. import config

API_URL = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
_HEADERS = {"User-Agent": "StackSignal/1.0 (personal technology radar)"}
_TRANSIENT = (406, 429, 500, 502, 503, 504)  # arXiv answers with these when it is busy or throttling
_last_call = 0.0


class ArxivError(RuntimeError):
    pass


def _space(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _arxiv_id(entry_id: str) -> tuple[str, str]:
    """('2409.01234', 'v2') from 'http://arxiv.org/abs/2409.01234v2' (old style ids keep their subject prefix)."""
    tail = entry_id.split("/abs/", 1)[-1]
    match = re.fullmatch(r"(.+?)(v\d+)?", tail)
    return (match.group(1), match.group(2) or "") if match else (tail, "")


def normalize(entry: ET.Element) -> dict:
    """Pure transform from one Atom <entry> to our normalized item shape."""
    paper_id, version = _arxiv_id(entry.findtext("a:id", "", _NS))
    authors = [_space(a.findtext("a:name", "", _NS)) for a in entry.findall("a:author", _NS)]
    categories = [c.get("term") for c in entry.findall("a:category", _NS) if c.get("term")]
    primary = entry.find("x:primary_category", _NS)
    pdf = next((link.get("href") for link in entry.findall("a:link", _NS) if link.get("title") == "pdf"), "")
    return {
        "source": "arxiv",
        "external_id": paper_id,
        "name": _space(entry.findtext("a:title", "", _NS)),
        "owner": (authors[0] + (" et al." if len(authors) > 1 else "")) if authors else "",
        "url": f"https://arxiv.org/abs/{paper_id}",
        "description": _space(entry.findtext("a:summary", "", _NS))[:1500],
        "topics": categories[:6],
        "stars": 0, "forks": 0, "downloads": 0, "likes": 0,  # papers carry no popularity signal
        "source_created_at": entry.findtext("a:published", None, _NS),
        "source_updated_at": entry.findtext("a:updated", None, _NS),
        "raw": {
            "authors": authors[:8], "primary_category": primary.get("term") if primary is not None else None,
            "categories": categories, "pdf": pdf, "version": version,
            "comment": _space(entry.findtext("x:comment", "", _NS))[:300],
            "journal_ref": _space(entry.findtext("x:journal_ref", "", _NS))[:200],
        },
    }


def _get(params: dict, transport=None) -> tuple[int, bytes]:
    """(status, body). Real requests go through urllib: arXiv answers 406 to httpx's TLS handshake
    here (same URL, same headers, urllib gets 200), so httpx is used only with a test transport."""
    if transport is not None:
        with httpx.Client(transport=transport, headers=_HEADERS, timeout=30) as client:
            resp = client.get(API_URL, params=params)
        return resp.status_code, resp.content
    request = urllib.request.Request(f"{API_URL}?{urllib.parse.urlencode(params)}", headers=_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ArxivError(f"arXiv is not reachable ({error.__class__.__name__})") from error


def _fetch(params: dict, transport=None) -> list[dict]:
    global _last_call
    for attempt in range(3):
        wait = _last_call + config.ARXIV_PAUSE * (1 if attempt == 0 else 2) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            status, body = _get(params, transport)
        finally:
            _last_call = time.monotonic()
        if status == 200:
            break
        if status not in _TRANSIENT or attempt == 2:
            raise ArxivError(f"arXiv request failed ({status})")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        raise ArxivError("arXiv answered with something that is not Atom") from error
    return [normalize(entry) for entry in root.findall("a:entry", _NS)
            if "/api/errors" not in entry.findtext("a:id", "", _NS)]


def _terms(text: str) -> str:
    """One or two words are searched as an exact phrase; longer queries as all their words together
    (a strict phrase such as "soft body simulation" finds almost nothing in a few months of papers)."""
    words = re.sub(r'["\\()]', " ", _space(text)).split()
    if len(words) <= 2:
        return f'all:"{" ".join(words)}"'
    return " AND ".join(f'all:"{w}"' if not w.isalnum() else f"all:{w}" for w in words[:5])


def search_papers(text: str, *, days: int | None = None, max_results: int = 15, transport=None) -> list[dict]:
    """Papers from the last `days` days (ARXIV_DAYS) whose title, abstract or authors match `text`, best match first."""
    days = config.ARXIV_DAYS if days is None else days
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=days)).strftime("%Y%m%d0000")
    query = f"{_terms(text)} AND submittedDate:[{since} TO {today.strftime('%Y%m%d')}2359]"
    return _fetch({"search_query": query, "start": 0, "max_results": max_results, "sortBy": "relevance",
                   "sortOrder": "descending"}, transport)


def get_paper(paper_id: str, *, transport=None) -> dict | None:
    """One paper by arXiv id (with or without a version), or None if there is no such paper."""
    found = _fetch({"id_list": paper_id, "max_results": 1}, transport)
    return found[0] if found else None
