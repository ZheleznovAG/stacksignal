"""arXiv papers, npm packages and PyPI packages: collectors, quality gate, scoring, project searches,
refresh, Add URL and the one-time default queries. Nothing here touches the network."""

import datetime

import pytest

from app import config, discovery, intake, maintenance, models, pipeline, queries, quality, scoring
from app.collectors import arxiv, npm, pypi
from tests.fakes import Web, arxiv_entry, npm_hit, pypi_doc
from app.utils import utcnow

NOW = utcnow()


# ---- arXiv ---------------------------------------------------------------------------------

def test_a_paper_is_normalised():
    web = Web()
    web.arxiv_papers["diarization"] = [arxiv_entry("2606.26144", title="Neural   Speaker\n Diarization",
                                                   authors=("Ada Lovelace", "Alan Turing"), categories=("cs.SD", "cs.LG"))]

    (paper,) = arxiv.search_papers("speaker diarization", transport=web.transport)

    assert paper["source"] == "arxiv" and paper["external_id"] == "2606.26144"  # the version is not part of the id
    assert paper["name"] == "Neural Speaker Diarization" and paper["owner"] == "Ada Lovelace et al."
    assert paper["url"] == "https://arxiv.org/abs/2606.26144" and paper["topics"] == ["cs.SD", "cs.LG"]
    assert paper["stars"] == paper["likes"] == paper["downloads"] == 0
    assert paper["raw"]["version"] == "v2" and paper["raw"]["primary_category"] == "cs.SD"
    assert paper["raw"]["pdf"].endswith("2606.26144v2")


@pytest.mark.parametrize("text, expected", [
    ("diarization", 'all:"diarization"'),
    ("speaker diarization", 'all:"speaker diarization"'),
    ("position based dynamics", "all:position AND all:based AND all:dynamics"),
    ("on-device OCR models", 'all:"on-device" AND all:OCR AND all:models'),
    ('quo"te (x) y z', "all:quo AND all:te AND all:x AND all:y AND all:z"),
])
def test_short_queries_are_phrases_and_longer_ones_are_all_their_words(text, expected):
    assert arxiv._terms(text) == expected


def test_the_search_asks_for_recent_papers_only():
    web = Web()
    arxiv.search_papers("speaker diarization", days=30, transport=web.transport)

    (request,) = web.requests
    assert "submittedDate" in request and "sortBy=relevance" in request


def test_a_busy_arxiv_is_retried_and_a_persistent_refusal_is_an_error():
    web = Web()
    web.arxiv_papers["diarization"] = [arxiv_entry("2606.1")]
    web.arxiv_statuses = [406, 503]
    assert len(arxiv.search_papers("diarization", transport=web.transport)) == 1

    web.arxiv_statuses = [406, 406, 406]
    with pytest.raises(arxiv.ArxivError):
        arxiv.search_papers("diarization", transport=web.transport)


def test_one_paper_by_id():
    web = Web()
    web.arxiv_by_id["1706.03762"] = arxiv_entry("1706.03762", title="Attention Is All You Need")

    assert arxiv.get_paper("1706.03762", transport=web.transport)["name"] == "Attention Is All You Need"
    assert arxiv.get_paper("0000.00000", transport=web.transport) is None


# ---- npm and PyPI -----------------------------------------------------------------------------

def test_npm_hits_carry_monthly_downloads_and_scoped_names_work():
    web = Web()
    web.npm_hits["ocr"] = [npm_hit("tesseract.js", 10_000_000, keywords=("ocr", "tesseract"))]
    web.npm_packages["@types/node"] = {"name": "@types/node", "version": "26.0.0", "description": "types",
                                       "license": "MIT", "_npmUser": {"name": "types"}}
    web.npm_monthly["@types/node"] = 900

    (hit,) = npm.search_packages("ocr", transport=web.transport)
    scoped = npm.get_package("@types/node", transport=web.transport)

    assert hit["downloads"] == 10_000_000 and hit["topics"] == ["ocr", "tesseract"] and hit["raw"]["license"] == "MIT"
    assert scoped["external_id"] == "@types/node" and scoped["downloads"] == 900 and scoped["raw"]["version"] == "26.0.0"
    assert npm.get_package("no-such-package", transport=web.transport) is None


def test_a_pypi_package_has_dates_and_a_repository():
    web = Web()
    web.pypi_docs["fastapi"] = pypi_doc("fastapi", "0.141.1")

    package = pypi.get_package("fastapi", transport=web.transport)

    assert package["raw"]["version"] == "0.141.1" and package["raw"]["repository"] == "https://github.com/o/fastapi"
    assert package["source_created_at"].startswith("2020-01-01") and package["source_updated_at"].startswith("2026-08-01")
    assert package["topics"][:2] == ["alpha", "beta"]
    assert pypi.get_package("nope", transport=web.transport) is None


# ---- gate and scoring ----------------------------------------------------------------------------

def test_the_gate_for_papers_and_packages():
    paper = {"source": "arxiv", "name": "x", "description": "short"}
    assert quality.gate(paper, targeted=True) == (False, "no_abstract")
    assert quality.gate({**paper, "description": "a" * 200}, targeted=True)[0]

    package = {"source": "npm", "name": "left-pad", "description": "pads", "topics": [], "downloads": 500}
    assert quality.gate(package, targeted=True)[0] and not quality.gate(package, targeted=False)[0]
    assert quality.gate({**package, "downloads": 50}, targeted=True) == (False, "little_traction")
    assert quality.gate({**package, "description": "", "downloads": 5000}, targeted=False) == (False, "no_description")
    assert quality.gate({"source": "pypi", "name": "anything"})[0]


def test_new_papers_and_popular_packages_score_sensibly():
    counts = dict(stars=0, forks=0, likes=0, downloads=0, raw={})
    fresh = models.Item(source="arxiv", external_id="p", name="p", owner="o", url="u", description="d", topics=[], **counts,
                        source_created_at=NOW - datetime.timedelta(days=5), source_updated_at=NOW - datetime.timedelta(days=5))
    old = models.Item(source="arxiv", external_id="q", name="q", owner="o", url="u", description="d", topics=[], **counts,
                      source_created_at=NOW - datetime.timedelta(days=200), source_updated_at=NOW - datetime.timedelta(days=200))
    package = models.Item(source="npm", external_id="n", name="n", owner="o", url="u", description="d", topics=[],
                          stars=0, forks=0, likes=0, downloads=500_000, raw={"license": "MIT"},
                          source_created_at=NOW - datetime.timedelta(days=700), source_updated_at=NOW - datetime.timedelta(days=3))

    assert scoring.compute_momentum(fresh) > scoring.compute_momentum(old)
    assert scoring.compute_momentum(package) > 0.3
    assert scoring.compute_maturity(package) > scoring.compute_maturity(fresh)


# ---- project searches -------------------------------------------------------------------------------

def _project(db, keywords=("speaker diarization", "position based dynamics"), **fields):
    project = models.Project(name="Audio", slug="audio", description="d", keywords=list(keywords), **fields)
    db.add(project)
    db.flush()
    return project


def test_arxiv_queries_are_research_topics_and_npm_ones_need_npm_in_the_stack(db_session):
    plain = _project(db_session)
    wanted = queries.heuristic_queries(plain, [plain])
    assert set(wanted["arxiv"]) == {"speaker diarization", "position based dynamics"} and wanted["npm"] == []

    plain.stack_packages = [{"name": "react", "ecosystem": "npm"}]
    assert queries.heuristic_queries(plain, [plain])["npm"] == ["speaker diarization"]


def test_a_run_finds_papers_for_a_project_and_says_why(db_session):
    _project(db_session)
    web = Web()
    web.arxiv_papers["speaker diarization"] = [arxiv_entry("2606.26144"), arxiv_entry("2606.99999", abstract="tiny")]

    run = pipeline.run_collection(db_session, transport=web.transport)

    paper = db_session.query(models.Item).filter_by(source=models.Source.ARXIV, external_id="2606.26144").one()
    assert paper.first_seen_at and paper.url == "https://arxiv.org/abs/2606.26144"
    assert db_session.query(models.Item).filter_by(external_id="2606.99999").count() == 0  # abstract too short
    via = db_session.query(models.ItemDiscovery).filter_by(item_id=paper.id).one()
    assert via.project_slug == "audio" and via.query_text == "speaker diarization"
    assert "arxiv" in run.sources


def test_when_arxiv_is_down_its_remaining_queries_are_skipped_and_the_rest_still_runs(db_session):
    _project(db_session)
    db_session.add_all([models.SearchQuery(project_slug="audio", source="arxiv", text=t, origin="user")
                        for t in ("first topic", "second topic")])
    db_session.flush()
    web = Web()
    web.arxiv_statuses = [500] * 20
    web.hf_search["speaker diarization"] = []

    _, stats = discovery.run_queries(db_session, transport=web.transport)

    assert stats["errors"] == 1  # the second arXiv query was not even tried
    assert sum("export.arxiv.org" in r for r in web.requests) == 3  # one query, three attempts


def test_due_queries_respect_the_per_source_budget(db_session):
    _project(db_session)
    for source in ("arxiv", "npm"):
        for i in range(3):
            db_session.add(models.SearchQuery(project_slug="audio", source=source, text=f"{source} {i}", origin="auto"))
    db_session.flush()

    due = discovery.due_queries(db_session, github_limit=0, hf_limit=0, arxiv_limit=2, npm_limit=1)

    assert sorted(q.source for q in due) == ["arxiv", "arxiv", "npm"]


def test_tracked_packages_get_fresh_numbers(db_session):
    _project(db_session)
    package = models.Item(source="npm", external_id="tesseract.js", name="tesseract.js", owner="o", url="u",
                          description="d", topics=[], downloads=1, status=models.Status.ASSESS,
                          last_seen_at=NOW - datetime.timedelta(days=3), raw={})
    db_session.add(package)
    db_session.flush()
    web = Web()
    web.npm_packages["tesseract.js"] = {"name": "tesseract.js", "version": "7.0.0", "description": "OCR"}
    web.npm_monthly["tesseract.js"] = 12345

    pipeline.run_collection(db_session, transport=web.transport)

    assert package.downloads == 12345 and package.last_seen_at > NOW - datetime.timedelta(minutes=5)


def test_existing_projects_get_arxiv_queries_once(db_session):
    project = _project(db_session)
    db_session.add(models.SearchQuery(project_slug=project.slug, source="github", text="speaker diarization", origin="auto"))
    db_session.flush()

    assert maintenance.add_arxiv_default_queries(db_session) == 2
    assert maintenance.add_arxiv_default_queries(db_session) == 0  # nothing is added twice
    assert {q.text for q in db_session.query(models.SearchQuery).filter_by(source="arxiv")} == {
        "speaker diarization", "position based dynamics"}


# ---- Add URL -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    ("https://arxiv.org/abs/1706.03762", ("arxiv", "1706.03762")),
    ("https://arxiv.org/abs/1706.03762v7", ("arxiv", "1706.03762")),
    ("https://arxiv.org/pdf/2606.26144v2.pdf", ("arxiv", "2606.26144")),
    ("https://arxiv.org/abs/cs/0501001", ("arxiv", "cs/0501001")),
    ("https://www.npmjs.com/package/tesseract.js", ("npm", "tesseract.js")),
    ("https://www.npmjs.com/package/@types/node/v/26.0.0", ("npm", "@types/node")),
    ("https://pypi.org/project/fastapi/", ("pypi", "fastapi")),
    ("https://pypi.org/project/fastapi/0.141.1/", ("pypi", "fastapi")),
])
def test_paper_and_package_links_are_understood(url, expected):
    assert intake.parse_url(url) == expected


@pytest.mark.parametrize("url", ["https://arxiv.org/list/cs.LG/recent", "https://www.npmjs.com/", "https://pypi.org/search/?q=x",
                                 "https://arxiv.org/abs/not-an-id"])
def test_other_paper_and_package_pages_are_refused(url):
    with pytest.raises(intake.IntakeError):
        intake.parse_url(url)


def test_adding_a_paper_by_link_puts_it_on_the_radar(db_session):
    _project(db_session)
    web = Web()
    web.arxiv_by_id["1706.03762"] = arxiv_entry("1706.03762", title="Attention Is All You Need")

    item, created = intake.add_url(db_session, "https://arxiv.org/abs/1706.03762", analyze=False, transport=web.transport)

    assert created and item.name == "Attention Is All You Need" and item.status == models.Status.WATCH
    with pytest.raises(intake.IntakeError) as missing:
        intake.add_url(db_session, "https://arxiv.org/abs/0000.00000", analyze=False, transport=web.transport)
    assert missing.value.code == "not_found"


def test_adding_a_package_by_link(db_session):
    web = Web()
    web.pypi_docs["fastapi"] = pypi_doc("fastapi")

    item, _ = intake.add_url(db_session, "https://pypi.org/project/fastapi/", analyze=False, transport=web.transport)

    assert item.source == models.Source.PYPI and item.raw["version"] == "1.0.0"


# ---- API and profile import ------------------------------------------------------------------------------

def test_queries_can_be_added_for_the_new_sources_only():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app import db as db_module
    from app.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db_module.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = override
    try:
        client = TestClient(app)
        client.post("/api/projects", json={"name": "Audio", "keywords": ["x"]})
        add = lambda source: client.post("/api/queries", json={"project_slug": "audio", "source": source, "text": "diarization"})
        assert add("arxiv").status_code == 200 and add("npm").status_code == 200
        assert add("pypi").status_code == 400 and add("gitlab").status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_the_scan_says_which_ecosystem_each_dependency_belongs_to(tmp_path):
    from app import profile_import
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"dependencies":{"react":"18"}}', encoding="utf-8")

    scan = profile_import.scan_folder(str(tmp_path))

    assert {d["name"]: d["ecosystem"] for d in scan["dependencies"]} == {"fastapi": "pypi", "react": "npm"}
