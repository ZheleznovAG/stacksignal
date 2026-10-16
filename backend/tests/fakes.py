"""A fake GitHub + Hugging Face for collection tests: an httpx.MockTransport that answers by URL,
plus builders for raw API objects. Nothing here touches the network."""

import httpx
from app.utils import utcnow


def gh_repo(full_name, stars=100, description="A useful tool", topics=("tool",), archived=False, owner_type="User",
            created_at="2026-09-01T00:00:00Z", pushed_at="2026-09-18T00:00:00Z", fork=False):
    owner, name = full_name.split("/")
    return {"full_name": full_name, "name": name, "owner": {"login": owner, "id": sum(map(ord, owner)), "type": owner_type},
            "html_url": f"https://github.com/{full_name}", "description": description, "topics": list(topics),
            "stargazers_count": stars, "forks_count": 3, "created_at": created_at,
            "pushed_at": pushed_at, "language": "C#", "license": {"spdx_id": "MIT"},
            "open_issues_count": 0, "archived": archived, "fork": fork}


def hf_model(model_id, likes=50, downloads=5000, pipeline="text-generation", library="transformers",
             tags=("a", "b", "c")):
    return {"id": model_id, "tags": list(tags), "downloads": downloads, "likes": likes,
            "createdAt": "2026-09-10T00:00:00Z", "lastModified": "2026-09-18T00:00:00Z",
            "pipeline_tag": pipeline, "library_name": library}


def arxiv_entry(paper_id, title="A paper about things", abstract="An abstract that is long enough to be worth reading, "
                "describing a method and its results on a benchmark.", authors=("Ada Lovelace", "Alan Turing"),
                categories=("cs.LG", "cs.CV"), published="2026-09-01T00:00:00Z"):
    """One Atom <entry> as arXiv sends it."""
    people = "".join(f"<author><name>{a}</name></author>" for a in authors)
    cats = "".join(f'<category term="{c}"/>' for c in categories)
    return (f"<entry><id>http://arxiv.org/abs/{paper_id}v2</id><updated>{published}</updated><published>{published}</published>"
            f"<title>{title}</title><summary>{abstract}</summary>{people}"
            f'<arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="{categories[0]}"/>{cats}'
            f'<link href="http://arxiv.org/pdf/{paper_id}v2" rel="related" type="application/pdf" title="pdf"/></entry>')


def arxiv_feed(*entries):
    return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
            + "".join(entries) + "</feed>")


def npm_hit(name, monthly=5000, description="A useful package", keywords=("tool",), version="1.2.3"):
    return {"package": {"name": name, "version": version, "description": description, "keywords": list(keywords),
                        "date": "2026-09-10T00:00:00.000Z", "license": "MIT",
                        "publisher": {"username": "someone"}, "links": {"npm": f"https://www.npmjs.com/package/{name}"}},
            "downloads": {"monthly": monthly, "weekly": monthly // 4}, "score": {"detail": {"popularity": 0.5}}}


def pypi_doc(name, version="1.0.0", summary="A python package"):
    upload = [{"upload_time_iso_8601": "2026-08-01T00:00:00.000000Z"}]
    return {"info": {"name": name, "version": version, "summary": summary, "keywords": "alpha, beta", "license": "MIT",
                     "requires_python": ">=3.10", "project_urls": {"Repository": f"https://github.com/o/{name}"},
                     "classifiers": ["Topic :: Software Development"]},
            "releases": {"0.1.0": [{"upload_time_iso_8601": "2020-01-01T00:00:00.000000Z"}], version: upload}, "urls": upload}


def gh_release(tag, name=None, days_ago=1, prerelease=False, draft=False, body="What changed in this release."):
    import datetime
    published = (utcnow() - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"tag_name": tag, "name": name or tag, "html_url": f"https://github.com/o/r/releases/tag/{tag}",
            "published_at": published, "prerelease": prerelease, "draft": draft, "body": body}


class Web:
    def __init__(self):
        self.gh_created: list[dict] = []      # answers the global "created recently" search
        self.gh_pushed: list[dict] = []       # answers the global "pushed recently" search
        self.gh_queries: dict[str, list] = {}  # search phrase -> repositories (first page)
        self.gh_queries_page2: dict[str, list] = {}  # search phrase -> repositories (second page)
        self.gh_repos: dict[str, dict] = {}   # full name -> repo (for GET /repos/...)
        self.hf_lists: dict[str, list] = {"likes7d": [], "trendingScore": []}
        self.hf_search: dict[str, list] = {}  # search text -> models
        self.hf_models: dict[str, dict] = {}  # id -> model (for GET /api/models/<id>)
        self.arxiv_papers: dict[str, list] = {}    # words of the search -> Atom <entry> strings
        self.arxiv_by_id: dict[str, str] = {}      # arXiv id -> Atom <entry> string
        self.arxiv_statuses: list[int] = []        # answers to give (then 200), e.g. [406, 406]
        self.npm_hits: dict[str, list] = {}        # search text -> hits
        self.npm_packages: dict[str, dict] = {}    # name -> "latest" document
        self.npm_monthly: dict[str, int] = {}
        self.pypi_docs: dict[str, dict] = {}       # name -> PyPI JSON document
        self.gh_releases: dict[str, list] = {}     # full name -> releases (newest first)
        self.gh_users: dict[str, dict] = {}        # login (lowercase) -> account (for GET /users/<login>)
        self.gh_starred: dict[str, list] = {}      # login -> [(starred_at, repo)], newest first
        self.gh_following: dict[str, list] = {}    # login -> accounts
        self.gh_user_repos: dict[str, list] = {}   # login -> own repositories, most recently pushed first
        self.github_rate_limited = False
        self.hf_fail: set[str] = set()       # search texts that make Hugging Face answer 500
        self.requests: list[str] = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url, params = request.url, dict(request.url.params)
        self.requests.append(f"{url.host}{url.path}?{url.query.decode()}")
        if url.host == "api.github.com":
            if self.github_rate_limited:
                return httpx.Response(403, json={"message": "API rate limit exceeded"},
                                      headers={"x-ratelimit-remaining": "0"})
            if url.path == "/search/repositories":
                query = params["q"]
                if "created:>=" in query:
                    return httpx.Response(200, json={"items": self.gh_created})
                if "in:name,description,topics" in query:
                    phrase = query.split(" in:name")[0].strip('"')
                    source = self.gh_queries_page2 if params.get("page") == "2" else self.gh_queries
                    if params.get("page") not in (None, "1", "2"):
                        source = {}
                    return httpx.Response(200, json={"items": source.get(phrase, [])})
                return httpx.Response(200, json={"items": self.gh_pushed})
            if url.path.startswith("/users/"):
                login, _, tail = url.path[len("/users/"):].partition("/")
                page, size = int(params.get("page", 1)), int(params.get("per_page", 30))
                if tail == "starred":
                    assert request.headers["accept"] == "application/vnd.github.star+json"
                    rows = [{"starred_at": at, "repo": repo} for at, repo in self.gh_starred.get(login, [])]
                elif tail == "following":
                    rows = self.gh_following.get(login, [])
                elif tail == "repos":
                    assert params.get("sort") == "pushed"
                    rows = self.gh_user_repos.get(login, [])
                else:
                    account = self.gh_users.get(login.lower())
                    return httpx.Response(200, json=account) if account else httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, json=rows[(page - 1) * size:page * size])
            if url.path.startswith("/repos/") and url.path.endswith("/releases"):
                return httpx.Response(200, json=self.gh_releases.get(url.path[len("/repos/"):-len("/releases")], []))
            if url.path.startswith("/repos/"):
                repo = self.gh_repos.get(url.path[len("/repos/"):])
                return httpx.Response(200, json=repo) if repo else httpx.Response(404, json={"message": "Not Found"})
        if url.host == "export.arxiv.org":
            if self.arxiv_statuses:
                status = self.arxiv_statuses.pop(0)
                if status != 200:
                    return httpx.Response(status)
            if "id_list" in params:
                entry = self.arxiv_by_id.get(params["id_list"])
                return httpx.Response(200, text=arxiv_feed(*([entry] if entry else [])))
            query = params.get("search_query", "")
            found = [e for words, entries in self.arxiv_papers.items() if all(w in query for w in words.split())
                     for e in entries]
            return httpx.Response(200, text=arxiv_feed(*found))
        if url.host == "registry.npmjs.org":
            if url.path == "/-/v1/search":
                return httpx.Response(200, json={"objects": self.npm_hits.get(params.get("text", ""), [])})
            name = url.path.removesuffix("/latest").lstrip("/").replace("%2F", "/").replace("%2f", "/")
            package = self.npm_packages.get(name)
            return httpx.Response(200, json=package) if package else httpx.Response(404, json={})
        if url.host == "api.npmjs.org":
            name = url.path.rsplit("last-month/", 1)[-1].replace("%2F", "/")
            return httpx.Response(200, json={"downloads": self.npm_monthly.get(name, 0)})
        if url.host == "pypi.org":
            doc = self.pypi_docs.get(url.path.split("/")[2])
            return httpx.Response(200, json=doc) if doc else httpx.Response(404, json={})
        if url.host == "huggingface.co":
            if url.path == "/api/models":
                if "search" in params:
                    if params["search"] in self.hf_fail:
                        return httpx.Response(500, json={"error": "boom"})
                    return httpx.Response(200, json=self.hf_search.get(params["search"], []))
                return httpx.Response(200, json=self.hf_lists.get(params["sort"], []))
            if url.path.startswith("/api/models/"):
                model = self.hf_models.get(url.path[len("/api/models/"):])
                return httpx.Response(200, json=model) if model else httpx.Response(404, json={})
        return httpx.Response(500, json={"error": f"unexpected request {url}"})


def gh_account(login, kind="User"):
    return {"login": login, "id": sum(map(ord, login)), "type": kind, "html_url": f"https://github.com/{login}"}
