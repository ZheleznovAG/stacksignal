from app import models, pipeline
from app.enrichment import Enrichment


def _make_item(db_session, status=models.Status.NEW) -> models.Item:
    item = models.Item(
        source=models.Source.GITHUB.value,
        external_id="owner/repo",
        name="repo",
        owner="owner",
        url="https://github.com/owner/repo",
        stars=100,
        raw={},
        status=status,
        momentum_score=0.9,
        maturity_score=0.9,
    )
    db_session.add(item)
    db_session.flush()
    return item


def _make_projects(db_session, *names) -> list[models.Project]:
    projects = [models.Project(name=n, slug=n.lower(), keywords=[]) for n in names]
    db_session.add_all(projects)
    db_session.flush()
    return projects


def test_apply_enrichment_deletes_stale_relevance(db_session, monkeypatch):
    item = _make_item(db_session)
    proj_a, proj_b = _make_projects(db_session, "ProjA", "ProjB")

    # First pass: relevant to both projects.
    monkeypatch.setattr(
        pipeline,
        "enrich_item",
        lambda item, projects: Enrichment(
            summary="s",
            why_interesting="w",
            relevance={
                "proja": {"score": 0.6, "rationale": "matches A"},
                "projb": {"score": 0.5, "rationale": "matches B"},
            },
        ),
    )
    pipeline._apply_enrichment(db_session, item, [proj_a, proj_b])
    db_session.flush()
    assert {r.project_id for r in item.relevances} == {proj_a.id, proj_b.id}

    # Second pass: no longer relevant to B — that stale row must be gone, not just unchanged.
    monkeypatch.setattr(
        pipeline,
        "enrich_item",
        lambda item, projects: Enrichment(
            summary="s2",
            why_interesting="w2",
            relevance={"proja": {"score": 0.7, "rationale": "still matches A"}},
        ),
    )
    pipeline._apply_enrichment(db_session, item, [proj_a, proj_b])
    db_session.flush()

    remaining = db_session.query(models.ItemProjectRelevance).filter_by(item_id=item.id).all()
    assert {r.project_id for r in remaining} == {proj_a.id}


def test_an_ai_analysis_auto_advances_a_new_item(db_session, monkeypatch):
    item = _make_item(db_session, status=models.Status.NEW)
    proj_a = _make_projects(db_session, "ProjA")[0]

    monkeypatch.setattr(
        pipeline,
        "enrich_item",
        lambda item, projects: Enrichment(
            summary="s", why_interesting="w", relevance={"proja": {"score": 0.6, "rationale": "r"}},
            provenance={"mode": "ai"},
        ),
    )
    pipeline._apply_enrichment(db_session, item, [proj_a])
    db_session.flush()

    assert item.status != models.Status.NEW


def test_a_keyword_only_analysis_never_moves_the_radar(db_session, monkeypatch):
    item = _make_item(db_session, status=models.Status.NEW)
    proj_a = _make_projects(db_session, "ProjA")[0]

    monkeypatch.setattr(
        pipeline,
        "enrich_item",
        lambda item, projects: Enrichment(
            summary="s", why_interesting="w", relevance={"proja": {"score": 0.95, "rationale": "keyword overlap"}},
            provenance={"mode": "heuristic"},
        ),
    )
    pipeline._apply_enrichment(db_session, item, [proj_a])
    db_session.flush()

    assert item.status == models.Status.NEW
    assert item.relevances[0].score == 0.95  # still ranked in Relevant, just not "decided"


def test_apply_enrichment_never_overrides_a_manually_set_status(db_session, monkeypatch):
    item = _make_item(db_session, status=models.Status.TRIAL)
    proj_a = _make_projects(db_session, "ProjA")[0]

    monkeypatch.setattr(
        pipeline,
        "enrich_item",
        lambda item, projects: Enrichment(
            summary="s", why_interesting="w", relevance={"proja": {"score": 0.9, "rationale": "r"}},
            provenance={"mode": "ai"},
        ),
    )
    pipeline._apply_enrichment(db_session, item, [proj_a])
    db_session.flush()

    assert item.status == models.Status.TRIAL
    # Relevance still gets refreshed even though status is left alone.
    assert len(item.relevances) == 1
