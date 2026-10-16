"""Sanity checks (not exact-score assertions) that the enriched project profiles are
expressive enough for heuristic keyword-overlap relevance to point in the right
direction. Mirrors the "Optional relevance sanity checks" in the project-profiles task:
this only checks relative ordering, never forces a specific score.
"""

from app import models
from app.enrichment import heuristic_enrich
from app.seed import apply_project_profiles
from tests.sample_profiles import PROJECT_PROFILES

import pytest


@pytest.fixture(autouse=True)
def bootstrap_examples(monkeypatch):
    from app import seed
    monkeypatch.setattr(seed, "PROJECT_PROFILES", PROJECT_PROFILES)


def _item(name: str, description: str, topics: list[str]) -> models.Item:
    return models.Item(
        source=models.Source.GITHUB.value,
        external_id=f"x/{name}",
        name=name,
        owner="x",
        url="https://github.com/x/" + name,
        description=description,
        topics=topics,
        raw={},
    )


def _score(relevance: dict, slug: str) -> float:
    return relevance.get(slug, {}).get("score", 0.0)


def test_full_body_ik_relevant_to_softbody_and_animation_demo_not_audio_or_pixel(db_session):
    apply_project_profiles(db_session)
    projects = db_session.query(models.Project).all()

    item = _item(
        "ik-toolkit",
        "A full-body IK and contact-aware animation toolkit for Unity with procedural posing.",
        ["full-body-ik", "inverse-kinematics", "contact-aware-animation", "unity"],
    )
    result = heuristic_enrich(item, projects)

    softbody_score = _score(result.relevance, "softbody")
    animation_score = _score(result.relevance, "animation-demo")
    audio_score = _score(result.relevance, "transcription-demo")
    pixel_score = _score(result.relevance, "sprite-lab")

    assert softbody_score > audio_score
    assert animation_score > audio_score
    assert softbody_score > pixel_score
    assert animation_score > pixel_score


def test_whisper_optimization_relevant_to_audio_and_chitalka_not_softbody(db_session):
    apply_project_profiles(db_session)
    projects = db_session.query(models.Project).all()

    item = _item(
        "fast-whisper-cpp",
        "A quantized whisper.cpp build for fast local speech-to-text transcription with timestamps.",
        ["whisper", "speech-to-text", "local-inference", "quantization"],
    )
    result = heuristic_enrich(item, projects)

    audio_score = _score(result.relevance, "transcription-demo")
    chitalka_score = _score(result.relevance, "chitalka")
    softbody_score = _score(result.relevance, "softbody")

    assert audio_score > softbody_score
    assert chitalka_score > softbody_score


def test_controlnet_character_consistency_relevant_to_sprite_demo_and_sprite_lab_not_learning(db_session):
    apply_project_profiles(db_session)
    projects = db_session.query(models.Project).all()

    item = _item(
        "consistent-sprites",
        "ControlNet + IP-Adapter pipeline for character-consistent sprite generation and sprite sheets.",
        ["controlnet", "ip-adapter", "sprite-generation", "character-consistency"],
    )
    result = heuristic_enrich(item, projects)

    sprite_score = _score(result.relevance, "sprite-demo")
    pixel_score = _score(result.relevance, "sprite-lab")
    learning_score = _score(result.relevance, "learning")

    assert sprite_score > learning_score
    assert pixel_score > learning_score
