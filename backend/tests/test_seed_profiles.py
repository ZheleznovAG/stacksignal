from app import models
from app.seed import apply_project_profiles
from tests.sample_profiles import PROJECT_PROFILES

import pytest


@pytest.fixture(autouse=True)
def bootstrap_examples(monkeypatch):
    from app import seed
    monkeypatch.setattr(seed, "PROJECT_PROFILES", PROJECT_PROFILES)

CANONICAL_NAMES = {p["name"] for p in PROJECT_PROFILES}
CANONICAL_SLUGS = {p["slug"] for p in PROJECT_PROFILES}


def test_public_install_starts_empty(db_session, monkeypatch):
    from app import seed
    monkeypatch.setattr(seed, "PROJECT_PROFILES", [])
    result = apply_project_profiles(db_session)
    assert result == {"created": [], "updated": [], "skipped": False}
    assert db_session.query(models.Project).count() == 0


def test_apply_creates_all_profiles_on_empty_db(db_session):
    result = apply_project_profiles(db_session)

    projects = db_session.query(models.Project).all()
    assert len(projects) == len(PROJECT_PROFILES) == 11
    assert {p.name for p in projects} == CANONICAL_NAMES
    assert {p.slug for p in projects} == CANONICAL_SLUGS
    assert result["skipped"] is False
    assert set(result["created"]) == CANONICAL_NAMES


def test_second_call_is_a_pure_noop(db_session):
    apply_project_profiles(db_session)
    result = apply_project_profiles(db_session)

    assert result == {"created": [], "updated": [], "skipped": True}
    assert db_session.query(models.Project).count() == len(PROJECT_PROFILES)


def test_edit_made_after_migration_survives_a_later_call(db_session):
    apply_project_profiles(db_session)

    softbody = db_session.query(models.Project).filter_by(slug="softbody").one()
    softbody.description = "My own hand-edited description."
    db_session.commit()

    apply_project_profiles(db_session)  # simulates the app restarting

    softbody_after = db_session.query(models.Project).filter_by(slug="softbody").one()
    assert softbody_after.description == "My own hand-edited description."


def test_delete_made_after_migration_survives_a_later_call(db_session):
    apply_project_profiles(db_session)

    sprite_lab = db_session.query(models.Project).filter_by(slug="sprite-lab").one()
    db_session.delete(sprite_lab)
    db_session.commit()

    apply_project_profiles(db_session)  # simulates the app restarting

    assert db_session.query(models.Project).filter_by(slug="sprite-lab").one_or_none() is None
    assert db_session.query(models.Project).count() == len(PROJECT_PROFILES) - 1


def test_fresh_db_still_gets_all_11_profiles(db_session):
    # A brand new install has no applied_migrations row yet, so the first call must
    # still do the full bootstrap.
    assert db_session.query(models.AppliedMigration).count() == 0
    apply_project_profiles(db_session)
    assert db_session.query(models.Project).count() == 11
    assert db_session.query(models.AppliedMigration).filter_by(name="project_profiles_v1").count() == 1


def test_apply_merges_into_existing_project_by_slug(db_session):
    db_session.add(
        models.Project(
            name="Review Crawler",
            slug="review-crawler",
            description="old terse description",
            keywords=["scraping", "docker", "some-old-tag"],
        )
    )
    db_session.commit()

    apply_project_profiles(db_session)

    rows = db_session.query(models.Project).filter_by(slug="review-crawler").all()
    assert len(rows) == 1
    project = rows[0]
    assert "some-old-tag" in project.keywords  # old keyword preserved
    assert "Playwright" in project.keywords  # new keyword merged in
    assert project.description.startswith("[active")


def test_apply_migrates_known_alias_instead_of_duplicating(db_session):
    db_session.add(
        models.Project(name="Dialogue Prototype", slug="dialogue-prototype", description="old", keywords=["chatbot"])
    )
    db_session.commit()

    apply_project_profiles(db_session)

    matches = db_session.query(models.Project).filter(
        models.Project.name.in_(["Dialogue Prototype", "Dialogue Demo"])
    ).all()
    assert len(matches) == 1
    assert matches[0].name == "Dialogue Demo"
    assert matches[0].slug == "dialogue-demo"
    assert "chatbot" in matches[0].keywords  # old keyword preserved through the rename


def test_apply_never_touches_a_manually_added_unrelated_project(db_session):
    db_session.add(models.Project(name="TestProj", slug="testproj", description="mine", keywords=["x"]))
    db_session.commit()

    apply_project_profiles(db_session)
    apply_project_profiles(db_session)  # and a simulated restart afterward

    project = db_session.query(models.Project).filter_by(slug="testproj").one()
    assert project.description == "mine"
    assert project.keywords == ["x"]
    total = db_session.query(models.Project).count()
    assert total == len(PROJECT_PROFILES) + 1


def test_softbody_and_animation_demo_remain_distinct(db_session):
    apply_project_profiles(db_session)
    softbody = db_session.query(models.Project).filter_by(slug="softbody").one()
    animation = db_session.query(models.Project).filter_by(slug="animation-demo").one()
    assert softbody.id != animation.id
    assert "tunnels" in softbody.description  # Softbody Demo = deformable tunnel/membrane concept
    assert "posing" in animation.description  # the other = contact/pose mechanics


def test_private_profile_file_is_merged_over_public_profiles(db_session, monkeypatch, tmp_path):
    import json
    from app import seed

    path = tmp_path / "private_profiles.json"
    path.write_text(json.dumps({"softbody": {"description": "Fuller local description.",
                                          "keywords": ["local-only keyword", "Unity"]}}), encoding="utf-8")
    monkeypatch.setattr(seed, "PRIVATE_PROFILES_PATH", path)

    apply_project_profiles(db_session)

    softbody = db_session.query(models.Project).filter_by(slug="softbody").one()
    assert "Fuller local description." in softbody.description
    assert "local-only keyword" in softbody.keywords
    assert [k.lower() for k in softbody.keywords].count("unity") == 1  # merged, not duplicated


def test_missing_or_broken_private_file_is_ignored(db_session, monkeypatch, tmp_path):
    from app import seed

    bad = tmp_path / "private_profiles.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(seed, "PRIVATE_PROFILES_PATH", bad)

    apply_project_profiles(db_session)
    assert db_session.query(models.Project).count() == len(PROJECT_PROFILES)
