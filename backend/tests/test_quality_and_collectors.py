"""The quality gate and the collectors' new abilities (search by phrase, fetch one item, rate limits)."""

import httpx
import pytest

from app import models, quality
from app.collectors import github, huggingface
from tests.fakes import Web, gh_repo, hf_model


def _gh(stars=100, description="A tool", topics=("x",), archived=False):
    return github.normalize(gh_repo("o/r", stars, description, topics, archived))


def _hf(name="model", likes=50, downloads=5000, pipeline="text-generation", library="transformers", tags=("a", "b")):
    return huggingface.normalize(hf_model(f"o/{name}", likes, downloads, pipeline, library, tags))


# ---- the gate ----------------------------------------------------------------------------

def test_github_global_and_targeted_star_floors():
    assert quality.gate(_gh(stars=49)) == (False, "few_stars")
    assert quality.gate(_gh(stars=50))[0]
    assert quality.gate(_gh(stars=14), targeted=True) == (False, "few_stars")
    assert quality.gate(_gh(stars=15), targeted=True)[0]  # niche but on-topic is welcome


def test_github_rejects_archived_and_contentless_repositories():
    assert quality.gate(_gh(archived=True)) == (False, "archived_repo")
    assert quality.gate(_gh(description="", topics=())) == (False, "no_description")
    assert quality.gate(_gh(description="", topics=("ml",)))[0]


@pytest.mark.parametrize("name", ["test", "my-test-model", "granite41-3b-squad-ratio-0.60-seed-42",
                                  "tmp_model", "foo-seed42", "dummy"])
def test_huggingface_rejects_experiment_names(name):
    assert quality.gate(_hf(name=name)) == (False, "junk_name")


@pytest.mark.parametrize("name", ["pytest-helper", "contest-winner", "latest-model", "Qwen3-27B"])
def test_names_that_merely_contain_test_are_fine(name):
    assert quality.gate(_hf(name=name))[0]


def test_huggingface_needs_traction_global_vs_targeted():
    assert quality.gate(_hf(likes=9, downloads=1999)) == (False, "little_traction")
    assert quality.gate(_hf(likes=10, downloads=0))[0]
    assert quality.gate(_hf(likes=0, downloads=2000))[0]
    assert quality.gate(_hf(likes=2, downloads=299), targeted=True) == (False, "little_traction")
    assert quality.gate(_hf(likes=3, downloads=0), targeted=True)[0]


def test_huggingface_needs_some_metadata():
    assert quality.gate(_hf(pipeline=None, library=None, tags=("only",))) == (False, "no_metadata")
    assert quality.gate(_hf(pipeline=None, library=None, tags=("a", "b")))[0]


def test_stored_items_can_be_judged_with_the_same_gate(db_session):
    junk = models.Item(source="huggingface", external_id="o/test", name="test", owner="o", url="u", description="",
                       topics=[], likes=0, downloads=0, raw={})
    good = models.Item(source="github", external_id="o/good", name="good", owner="o", url="u", description="d",
                       topics=["x"], stars=500, raw={})
    db_session.add_all([junk, good])
    db_session.flush()

    assert quality.gate(quality.item_payload(junk))[0] is False
    assert quality.gate(quality.item_payload(good))[0] is True


def test_engagement_means_status_feedback_or_ai(db_session):
    item = models.Item(source="github", external_id="o/e", name="e", owner="o", url="u", description="d",
                       topics=[], raw={}, status=models.Status.NEW)
    db_session.add(item)
    db_session.flush()
    assert quality.has_engagement(item, False) is False
    assert quality.has_engagement(item, True) is False  # the AI read it, but found nothing strongly relevant in it
    project = models.Project(name="P", slug="p", description="d", keywords=["k"])
    db_session.add(project)
    db_session.flush()
    db_session.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=0.9, rationale="r"))
    db_session.flush()
    db_session.expire(item, ["relevances"])
    assert quality.has_engagement(item, True) is True  # read AND strongly relevant
    db_session.query(models.ItemProjectRelevance).delete()
    db_session.flush()
    db_session.expire(item, ["relevances"])
    item.status = models.Status.WATCH
    assert quality.has_engagement(item, False) is True
    item.status = models.Status.NEW
    db_session.add(models.ItemFeedback(item_id=item.id, kind="note", note="x"))
    db_session.flush()
    db_session.expire(item, ["feedback"])
    assert quality.has_engagement(item, False) is True


# ---- GitHub ------------------------------------------------------------------------------

def test_github_search_builds_a_targeted_query():
    web = Web()
    web.gh_queries["soft body physics"] = [gh_repo("a/softbody", 40)]

    results = github.search_repos("soft body physics", transport=web.transport)

    assert [r["external_id"] for r in results] == ["a/softbody"]
    query = web.requests[0]
    assert "%22soft+body+physics%22" in query or "%22soft%20body%20physics%22" in query  # quoted phrase
    assert "in%3Aname%2Cdescription%2Ctopics" in query and "fork%3Afalse" in query and "archived%3Afalse" in query
    assert "stars%3A%3E%3D15" in query and "pushed%3A%3E%3D" in query


def test_github_single_word_queries_are_not_quoted():
    web = Web()
    github.search_repos("xpbd", transport=web.transport)
    assert "%22" not in web.requests[0]


def test_github_rate_limit_is_reported_not_swallowed():
    web = Web()
    web.github_rate_limited = True
    with pytest.raises(github.RateLimited):
        github.search_repos("anything", transport=web.transport)
    with pytest.raises(github.RateLimited):
        github.get_repo("o/r", transport=web.transport)


def test_github_other_errors_are_ordinary_errors():
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(RuntimeError) as caught:
        github.search_repos("x", transport=transport)
    assert not isinstance(caught.value, github.RateLimited)


def test_github_get_repo_returns_fresh_numbers_or_none():
    web = Web()
    web.gh_repos["o/r"] = gh_repo("o/r", stars=777)

    assert github.get_repo("o/r", transport=web.transport)["stars"] == 777
    assert github.get_repo("o/gone", transport=web.transport) is None


def test_github_global_lists_exclude_forks_and_archived():
    web = Web()
    web.gh_created = [gh_repo("a/new", 200)]
    web.gh_pushed = [gh_repo("b/active", 900), gh_repo("a/new", 200)]

    results = github.collect(transport=web.transport)

    assert sorted(r["external_id"] for r in results) == ["a/new", "b/active"]  # de-duplicated
    assert all("fork%3Afalse" in r and "archived%3Afalse" in r for r in web.requests)


# ---- Hugging Face ------------------------------------------------------------------------

def test_huggingface_global_lists_no_longer_include_the_newest_models():
    web = Web()
    web.hf_lists["likes7d"] = [hf_model("a/liked")]
    web.hf_lists["trendingScore"] = [hf_model("a/trending"), hf_model("a/liked")]

    results = huggingface.collect(transport=web.transport)

    assert sorted(r["external_id"] for r in results) == ["a/liked", "a/trending"]
    requested = {r for r in web.requests}
    assert not any("createdAt" in r for r in requested)
    assert len(web.requests) == 2  # likes7d and trendingScore, nothing else


def test_huggingface_search_uses_the_phrase_and_recent_downloads():
    web = Web()
    web.hf_search["whisper"] = [hf_model("o/whisper-fast", 30, 9000)]

    results = huggingface.search_models("  whisper ", transport=web.transport)

    assert [r["external_id"] for r in results] == ["o/whisper-fast"]
    assert "search=whisper" in web.requests[0] and "sort=downloads" in web.requests[0]


def test_huggingface_get_model_returns_numbers_or_none_and_raises_on_errors():
    web = Web()
    web.hf_models["o/m"] = hf_model("o/m", likes=99)

    assert huggingface.get_model("o/m", transport=web.transport)["likes"] == 99
    assert huggingface.get_model("o/gone", transport=web.transport) is None
    with pytest.raises(RuntimeError):
        huggingface.get_model("o/m", transport=httpx.MockTransport(lambda request: httpx.Response(500)))
