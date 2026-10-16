"""List endpoints serialise many items at once. Loading each item's relations one by one made the
number of SQL statements grow with the list (about 9 per item); ser_items() loads them in a fixed
number of batched queries. These tests pin that, so an innocent new field in ser_item() cannot
quietly bring the N+1 back."""

from sqlalchemy import event

from app import models
from app.serializers import ser_item, ser_items
from app.utils import utcnow


def _world(db, n):
    project = models.Project(name="Chitalka", slug="chitalka", description="d", keywords=["ocr"])
    other = models.Project(name="Softbody Demo", slug="softbody", description="d", keywords=["unity"])
    cap = models.Capability(name="OCR", slug="ocr")
    db.add_all([project, other, cap])
    db.flush()
    now = utcnow()
    items = []
    for k in range(n):
        item = models.Item(source="github", external_id=f"o/r{k}", name=f"r{k}", owner="o", url=f"https://github.com/o/r{k}")
        db.add(item)
        db.flush()
        db.add_all([
            models.ItemCapability(item_id=item.id, capability_id=cap.id, confidence=0.9),
            models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=0.8, rationale="r"),
            models.ItemProjectDecision(item_id=item.id, project_id=other.id, status="assess", goal="g"),
            models.ItemRelease(item_id=item.id, tag="v1", published_at=now),
            models.StatusHistory(item_id=item.id, status="new", note="Discovered"),
            models.ItemFeedback(item_id=item.id, kind="vote", project_slug="chitalka", verdict=1),
            models.ItemAnalysisState(item_id=item.id, provenance={"mode": "ai"}),
            models.ItemDiscovery(item_id=item.id, project_slug="chitalka", query_text="ocr"),
        ])
        items.append(item)
    db.commit()
    return [i.id for i in items]


def _count_statements(db):
    counter = {"n": 0}

    def before(*_args):
        counter["n"] += 1

    event.listen(db.get_bind(), "before_cursor_execute", before)
    return counter, lambda: event.remove(db.get_bind(), "before_cursor_execute", before)


def _serialise_fresh(db, ids):
    db.expunge_all()  # as in a request: nothing is loaded yet
    items = db.query(models.Item).filter(models.Item.id.in_(ids)).all()
    return ser_items(db, items)


def test_serialising_a_list_takes_a_constant_number_of_queries(db_session):
    small = _world(db_session, 2)
    counter, stop = _count_statements(db_session)
    _serialise_fresh(db_session, small)
    few = counter["n"]
    stop()

    big = _world_more(db_session, 40)
    counter, stop = _count_statements(db_session)
    _serialise_fresh(db_session, big)
    many = counter["n"]
    stop()

    assert many == few, f"{few} statements for 2 items but {many} for 40: something loads per item"
    assert many <= 15


def _world_more(db, n):
    project = db.query(models.Project).filter_by(slug="chitalka").one()
    other = db.query(models.Project).filter_by(slug="softbody").one()
    cap = db.query(models.Capability).one()
    ids = []
    for k in range(n):
        item = models.Item(source="github", external_id=f"p/q{k}", name=f"q{k}", owner="p", url=f"https://github.com/p/q{k}")
        db.add(item)
        db.flush()
        db.add_all([
            models.ItemCapability(item_id=item.id, capability_id=cap.id, confidence=0.9),
            models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=0.8, rationale="r"),
            models.ItemProjectDecision(item_id=item.id, project_id=other.id, status="assess", goal="g"),
            models.ItemFeedback(item_id=item.id, kind="vote", project_slug="chitalka", verdict=1),
            models.ItemAnalysisState(item_id=item.id, provenance={"mode": "ai"}),
            models.ItemDiscovery(item_id=item.id, project_slug="chitalka", query_text="ocr"),
        ])
        ids.append(item.id)
    db.commit()
    return ids


def test_batched_serialisation_matches_the_one_by_one_result(db_session):
    ids = _world(db_session, 3)
    batched = _serialise_fresh(db_session, ids)
    db_session.expunge_all()
    single = [ser_item(db_session.get(models.Item, i)) for i in ids]
    assert batched == single
    assert batched[0]["ai_analyzed"] is True
    assert batched[0]["found_via"] == [{"project_slug": "chitalka", "query": "ocr"}]
