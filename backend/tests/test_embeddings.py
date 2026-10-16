"""Local embeddings: never touches the network or a real ONNX model here (EMBEDDINGS_ENABLED=0,
see conftest.py) -- embed() itself is monkeypatched with a small deterministic stand-in, the same
way test_chat.py replaces the AI gateway."""

from app import chat, embeddings, models, search_index


def _fake_vector(text: str) -> list[float]:
    """A tiny deterministic 3-d embedding: texts sharing a tag word point the same way."""
    tags = {"speech": [1.0, 0.0, 0.0], "shader": [0.0, 1.0, 0.0], "unrelated": [0.0, 0.0, 1.0]}
    for tag, vector in tags.items():
        if tag in text:
            return vector
    return [0.0, 0.0, 0.0]


def _fake_embed(texts):
    return [_fake_vector(t) for t in texts]


def _item(db, name, description) -> models.Item:
    entry = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url=f"https://x/{name}",
                        description=description, topics=[], raw={})
    db.add(entry)
    db.flush()
    return entry


def test_embed_is_disabled_in_tests():
    assert embeddings.embed(["anything"]) is None


def test_mean_pool_ignores_padded_positions():
    tokens = [[[1.0, 1.0], [9.0, 9.0], [3.0, 3.0]]]  # middle token is padding
    mask = [[1, 0, 1]]
    assert embeddings._mean_pool(tokens, mask) == [[2.0, 2.0]]


def test_normalize_produces_unit_vectors():
    [normalized] = embeddings._normalize([[3.0, 4.0]])
    assert abs(sum(v * v for v in normalized) ** 0.5 - 1.0) < 1e-9


def test_normalize_converts_numpy_scalars_to_plain_floats():
    """onnxruntime returns numpy.float32; the JSON column ItemEmbedding.vector cannot store it."""
    import numpy as np

    [normalized] = embeddings._normalize([np.array([3.0, 4.0], dtype=np.float32)])
    assert all(type(v) is float for v in normalized)


def test_ensure_embeddings_computes_one_row_per_item(db_session, monkeypatch):
    monkeypatch.setattr(embeddings, "embed", _fake_embed)
    _item(db_session, "whisper-fast", "local speech recognition")
    _item(db_session, "shader-lib", "gpu shader toolkit")
    db_session.flush()

    computed = embeddings.ensure_embeddings(db_session)

    assert computed == 2
    assert db_session.query(models.ItemEmbedding).count() == 2


def test_embedding_input_contains_the_name_and_rename_invalidates_it(db_session, monkeypatch):
    item = _item(db_session, "system-design-primer", "A collection of engineering notes")
    texts = []
    monkeypatch.setattr(embeddings, "embed", lambda batch: texts.extend(batch) or _fake_embed(batch))
    assert "system-design-primer" in search_index.item_text(item)
    assert embeddings.ensure_embeddings(db_session) == 1
    assert "system-design-primer" in texts[-1]
    item.name = "architecture-primer"
    assert embeddings.ensure_embeddings(db_session) == 1
    assert "architecture-primer" in texts[-1]


def test_ensure_embeddings_skips_unchanged_items(db_session, monkeypatch):
    monkeypatch.setattr(embeddings, "embed", _fake_embed)
    _item(db_session, "whisper-fast", "local speech recognition")
    db_session.flush()
    embeddings.ensure_embeddings(db_session)

    calls = []
    monkeypatch.setattr(embeddings, "embed", lambda texts: calls.extend(texts) or _fake_embed(texts))
    computed_again = embeddings.ensure_embeddings(db_session)

    assert computed_again == 0
    assert calls == []


def test_embedding_helper_never_commits_caller_transaction(db_session, monkeypatch):
    monkeypatch.setattr(embeddings, "embed", _fake_embed)
    monkeypatch.setattr(embeddings, "BATCH_SIZE", 1)
    for name in ("one", "two"):
        _item(db_session, name, "speech recognition")

    assert embeddings.ensure_embeddings(db_session) == 2
    db_session.rollback()

    assert db_session.query(models.Item).count() == 0
    assert db_session.query(models.ItemEmbedding).count() == 0


def test_ensure_embeddings_recomputes_after_a_text_change(db_session, monkeypatch):
    monkeypatch.setattr(embeddings, "embed", _fake_embed)
    item = _item(db_session, "whisper-fast", "local speech recognition")
    db_session.flush()
    embeddings.ensure_embeddings(db_session)

    item.description = "gpu shader toolkit now"
    db_session.flush()
    computed = embeddings.ensure_embeddings(db_session)

    assert computed == 1
    row = db_session.get(models.ItemEmbedding, item.id)
    assert row.vector == _fake_vector("gpu shader toolkit now")


def test_semantic_scores_ranks_by_similarity_and_applies_the_floor(db_session, monkeypatch):
    monkeypatch.setattr(embeddings, "embed", _fake_embed)
    speech = _item(db_session, "whisper-fast", "local speech recognition")
    other_speech = _item(db_session, "speech-tool-2", "another speech thing")
    unrelated = _item(db_session, "cookies", "an unrelated recipe")
    db_session.flush()
    embeddings.ensure_embeddings(db_session)

    scores = embeddings.semantic_scores(db_session, "speech transcription question")

    assert scores[speech.id] == scores[other_speech.id] == 1.0  # identical fake vectors
    assert unrelated.id not in scores  # cosine 0.0 with the query vector, below MIN_SIMILARITY


def test_semantic_scores_ignores_archived_items(db_session, monkeypatch):
    monkeypatch.setattr(embeddings, "embed", _fake_embed)
    item = _item(db_session, "whisper-fast", "local speech recognition")
    db_session.flush()
    embeddings.ensure_embeddings(db_session)
    item.archived = True
    db_session.flush()

    assert item.id not in embeddings.semantic_scores(db_session, "speech question")


def test_chat_candidates_include_semantic_matches_missed_by_keywords(db_session, monkeypatch):
    monkeypatch.setattr(chat.config, "CHAT_FTS_MIN_ITEMS", 1)
    semantic_only = models.Item(source="github", external_id="o/semantic-only", name="semantic-only", owner="o",
                                url="https://x", description="totally different words", topics=[], raw={},
                                momentum_score=0.1)
    db_session.add(semantic_only)
    for i in range(3):
        db_session.add(models.Item(source="github", external_id=f"o/filler{i}", name=f"filler{i}", owner="o",
                                   url="https://x", description="filler content", topics=[], raw={}))
    db_session.flush()

    candidates = chat._candidates(db_session, {"nomatchstem"}, set(), False, {semantic_only.id: 0.9})

    assert semantic_only in candidates


def test_chat_retrieve_ranks_a_semantic_only_match_above_high_momentum_noise(db_session, monkeypatch):
    monkeypatch.setattr(chat.config, "CHAT_FTS_MIN_ITEMS", 1)
    semantic_only = models.Item(source="github", external_id="o/semantic-only", name="semantic-only", owner="o",
                                url="https://x", description="totally different words", topics=[], raw={},
                                momentum_score=0.1)
    db_session.add(semantic_only)
    for i in range(5):
        db_session.add(models.Item(source="github", external_id=f"o/noise{i}", name=f"noise{i}", owner="o",
                                   url="https://x", description="filler content", topics=[], raw={},
                                   momentum_score=0.9))
    db_session.flush()
    monkeypatch.setattr(chat.embeddings, "semantic_scores", lambda db, query: {semantic_only.id: 0.9})

    ranked = chat._retrieve(db_session, "a question with no shared words")

    assert ranked[0].id == semantic_only.id


class _FakeStream:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        yield self.body


def _pin(monkeypatch, tmp_path, body: bytes, sha256: str):
    dest = tmp_path / "model.onnx"
    monkeypatch.setattr(embeddings, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(embeddings, "_FILES", (("model.onnx", dest, sha256),))
    monkeypatch.setattr(embeddings.httpx, "stream", lambda *a, **k: _FakeStream(body))
    return dest


def test_a_download_matching_its_checksum_is_kept(monkeypatch, tmp_path):
    import hashlib
    dest = _pin(monkeypatch, tmp_path, b"model bytes", hashlib.sha256(b"model bytes").hexdigest())
    assert embeddings._download() is True
    assert dest.read_bytes() == b"model bytes"


def test_a_download_with_the_wrong_checksum_is_thrown_away(monkeypatch, tmp_path):
    dest = _pin(monkeypatch, tmp_path, b"tampered", "0" * 64)
    assert embeddings._download() is False
    assert not dest.exists() and not list(tmp_path.glob("*.part"))


def test_an_oversized_download_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(embeddings, "_MAX_DOWNLOAD_BYTES", 4)
    dest = _pin(monkeypatch, tmp_path, b"far too big", "0" * 64)
    assert embeddings._download() is False
    assert not dest.exists()


def test_the_model_url_is_pinned_to_a_commit():
    assert "/resolve/main" not in embeddings._MODEL_BASE_URL
    assert len(embeddings._MODEL_REVISION) == 40
