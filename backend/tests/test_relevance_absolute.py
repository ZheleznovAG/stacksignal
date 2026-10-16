"""Absolute-range sanity checks for heuristic relevance scoring, not just relative
ordering (see test_relevance_sanity.py for that). These exist because the original
formula (`overlap / len(project.keywords)`) made scores worse the richer a project's
keyword list got — a real bug that relative-ordering tests alone didn't catch, since
ordering between projects can stay "correct" even while every absolute number is too
small to clear the app's own relevance thresholds (`/api/relevant`'s 0.1, Opportunities'
0.25).
"""

from app import models
from app.enrichment import heuristic_enrich

RELEVANT_THRESHOLD = 0.1
OPPORTUNITY_THRESHOLD = 0.25


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


def _project(name: str, slug: str, keywords: list[str]) -> models.Project:
    return models.Project(name=name, slug=slug, keywords=keywords)


def test_richer_keyword_list_does_not_penalize_a_matched_score():
    # Same specific overlapping keyword, but "Big" also lists 50 unrelated keywords
    # that this item does not match. If profile size were still penalizing score
    # (the original bug), Big would score noticeably lower than Small for the exact
    # same match.
    small = _project("Small", "small", ["full-body ik"])
    big = _project(
        "Big", "big", ["full-body ik"] + [f"filler keyword {i}" for i in range(50)]
    )
    other = _project("Other", "other", ["completely unrelated topic"])
    projects = [small, big, other]

    item = _item(
        "ik-toolkit",
        "A full-body IK toolkit.",
        ["full-body-ik"],
    )
    result = heuristic_enrich(item, projects)

    assert result.relevance["small"]["score"] == result.relevance["big"]["score"]


def test_strong_multi_keyword_match_reaches_the_practical_relevance_range():
    softbody = _project(
        "Softbody Demo",
        "softbody",
        [
            "unity", "soft-body physics", "full-body IK", "contact-aware animation",
            "procedural animation", "GPU simulation",
        ],
    )
    unrelated = _project("Unrelated", "unrelated", ["cobol", "mainframe batch jobs"])
    projects = [softbody, unrelated]

    item = _item(
        "ik-toolkit",
        "A full-body IK and contact-aware animation toolkit with procedural animation.",
        ["full-body-ik", "contact-aware-animation", "procedural-animation"],
    )
    result = heuristic_enrich(item, projects)

    score = result.relevance["softbody"]["score"]
    assert score >= OPPORTUNITY_THRESHOLD  # clears the Opportunities bar
    assert score >= 0.5  # comfortably in practical Assess/Trial territory


def test_single_common_keyword_match_stays_weak_not_assess():
    # "unity" shared by many projects should be a weak signal on its own, not enough
    # to look like a confident multi-keyword match.
    projects = [
        _project("Softbody Demo", "softbody", ["unity", "soft-body physics"]),
        _project("Learning Plan", "learning", ["unity", "android"]),
        _project("NicheGame", "niche-game", ["unity", "2d platformer"]),
    ]
    item = _item("generic-unity-tool", "Some generic Unity editor utility.", ["unity"])
    result = heuristic_enrich(item, projects)

    for slug in ("softbody", "learning", "niche-game"):
        assert result.relevance[slug]["score"] < 0.3  # below the ASSESS threshold


def test_matched_keywords_still_clear_the_relevant_endpoint_threshold():
    projects = [_project("Audio", "transcription-demo", ["whisper", "speech-to-text", "diarization"])]
    item = _item(
        "fast-whisper",
        "A fast local speech-to-text transcription tool built on whisper with diarization support.",
        ["whisper", "speech-to-text", "diarization"],
    )
    result = heuristic_enrich(item, projects)
    assert result.relevance["transcription-demo"]["score"] > RELEVANT_THRESHOLD
