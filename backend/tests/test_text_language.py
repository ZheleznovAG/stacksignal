"""AI reading texts are requested in Russian with technical terms kept in English, while every
identifier stays English; switching the language must not reuse analyses written in the other one."""

from app import ai_analysis, config, enrichment, models


def test_russian_rule_is_added_and_names_the_fields(monkeypatch):
    monkeypatch.setattr(config, "AI_TEXT_LANG", "ru")
    prompt = ai_analysis.with_language("BASE", '"summary"')
    assert prompt.startswith("BASE") and "Russian" in prompt and '"summary"' in prompt
    assert "capability names" in prompt and "fine-tuning" in prompt  # what must stay English


def test_english_leaves_the_prompt_untouched(monkeypatch):
    monkeypatch.setattr(config, "AI_TEXT_LANG", "en")
    assert ai_analysis.with_language("BASE", '"summary"') == "BASE"


def test_cache_key_and_fingerprint_depend_on_the_language(monkeypatch, db_session):
    monkeypatch.setattr(config, "AI_TEXT_LANG", "ru")
    ru_version = ai_analysis.lang_version("v1")
    item = models.Item(source="github", external_id="a/b", name="b", owner="a", url="u", description="d", topics=[])
    ru_fp = enrichment.item_fingerprint(item, [])
    monkeypatch.setattr(config, "AI_TEXT_LANG", "en")
    assert ai_analysis.lang_version("v1") != ru_version
    assert enrichment.item_fingerprint(item, []) != ru_fp  # old English analyses get redone in Russian


def test_invalid_language_is_rejected(monkeypatch):
    import importlib
    monkeypatch.setenv("AI_TEXT_LANG", "de")
    try:
        importlib.reload(config)
        raised = False
    except ValueError:
        raised = True
    finally:
        monkeypatch.delenv("AI_TEXT_LANG")
        importlib.reload(config)
    assert raised
