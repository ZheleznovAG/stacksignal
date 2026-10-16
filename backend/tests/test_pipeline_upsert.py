from app import models, pipeline


def _payload(**overrides):
    base = {
        "source": "github",
        "external_id": "owner/repo",
        "name": "repo",
        "owner": "owner",
        "url": "https://github.com/owner/repo",
        "description": "a repo",
        "topics": ["ai"],
        "stars": 100,
        "forks": 10,
        "downloads": 0,
        "likes": 0,
        "source_created_at": "2026-01-01T00:00:00Z",
        "source_updated_at": "2026-01-02T00:00:00Z",
        "raw": {},
    }
    base.update(overrides)
    return base


def test_upsert_creates_new_item_with_discovered_history(db_session):
    item, is_new = pipeline._upsert_item(db_session, _payload())
    db_session.flush()

    assert is_new is True
    assert item.status == models.Status.NEW

    history_rows = db_session.query(models.StatusHistory).filter_by(item_id=item.id).all()
    assert len(history_rows) == 1
    assert history_rows[0].note == "Discovered"
    assert history_rows[0].status == models.Status.NEW


def test_upsert_same_external_id_updates_in_place_without_new_history(db_session):
    item1, is_new1 = pipeline._upsert_item(db_session, _payload(stars=100))
    db_session.flush()

    item2, is_new2 = pipeline._upsert_item(db_session, _payload(stars=250))
    db_session.flush()

    assert is_new1 is True
    assert is_new2 is False
    assert item1.id == item2.id
    assert item2.stars == 250

    total_items = db_session.query(models.Item).count()
    assert total_items == 1

    history_rows = db_session.query(models.StatusHistory).filter_by(item_id=item2.id).all()
    assert len(history_rows) == 1  # no extra history entry just from an update


def test_upsert_different_source_same_external_id_are_distinct_items(db_session):
    gh_item, _ = pipeline._upsert_item(db_session, _payload(source="github", external_id="x/y"))
    hf_item, _ = pipeline._upsert_item(db_session, _payload(source="huggingface", external_id="x/y"))
    db_session.flush()

    assert gh_item.id != hf_item.id
    assert db_session.query(models.Item).count() == 2
