"""Local sentence embeddings (sentence-transformers/all-MiniLM-L6-v2, quantized ONNX), used to
close the gap the retrieval-quality measurement found: a keyword-only question ("what handles
software architecture and interview prep?") shares almost no word stems with an item's own text
("system-design-primer"), so chat._retrieve() misses it even though the two are obviously about
the same thing. Embeddings add a semantic candidate path next to that keyword search; they never
replace it, and everything still works with no embeddings at all (see embed(), which returns None
rather than raising when the model is unavailable).

The model (~23 MB) and its tokenizer are fetched once from Hugging Face into data/models/ on first
use, exactly like the SQLite database lives under data/: nothing to install, nothing committed to
git. A machine with no network yet, or where the download failed, just runs without semantic
candidates -- the same "keyword mode" fallback the rest of the app already relies on for AI.
"""

import hashlib
import logging
import threading

import httpx
from sqlalchemy.orm import Session

from . import config, models
from .config import ROOT_DIR
from .utils import utcnow

logger = logging.getLogger(__name__)

MODEL_DIR = ROOT_DIR / "data" / "models" / "all-MiniLM-L6-v2"
MODEL_FILE = MODEL_DIR / "model.onnx"
TOKENIZER_FILE = MODEL_DIR / "tokenizer.json"
# A fixed commit, not "main": the same StackSignal version always gets the same bytes, and a file
# that does not match its SHA-256 (tampered, truncated, or changed upstream) is thrown away.
_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
_MODEL_BASE_URL = f"https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/{_MODEL_REVISION}"
_FILES = (  # (path in the repository, where it goes, SHA-256)
    ("onnx/model_quint8_avx2.onnx", MODEL_FILE, "b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21"),
    ("tokenizer.json", TOKENIZER_FILE, "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037"),
)
_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024  # the model is ~23 MB; anything far bigger is not it
DIMENSIONS = 384

_lock = threading.Lock()
_session = None  # onnxruntime.InferenceSession, loaded lazily
_tokenizer = None  # tokenizers.Tokenizer, loaded lazily
_unavailable = False  # sticky: do not retry a download or load that already failed this run


class _BadDownload(Exception):
    pass


def _download() -> bool:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for path, dest, sha256 in _FILES:
        if dest.exists():
            continue
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            digest, size = hashlib.sha256(), 0
            with httpx.stream("GET", f"{_MODEL_BASE_URL}/{path}", follow_redirects=True, timeout=300) as response:
                response.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > _MAX_DOWNLOAD_BYTES:
                            raise _BadDownload(f"{path} is larger than {_MAX_DOWNLOAD_BYTES} bytes")
                        digest.update(chunk)
                        f.write(chunk)
            if digest.hexdigest() != sha256:
                raise _BadDownload(f"{path} does not match its SHA-256")
            tmp.replace(dest)
        except (httpx.HTTPError, _BadDownload, OSError) as error:
            logger.warning("Could not download the embedding model: %s", error)
            tmp.unlink(missing_ok=True)
            return False
    return True


def _load() -> bool:
    """Load the ONNX session and tokenizer, downloading them first if this is the first use."""
    global _session, _tokenizer, _unavailable
    if not config.EMBEDDINGS_ENABLED:
        return False
    if _session is not None:
        return True
    if _unavailable:
        return False
    with _lock:
        if _session is not None:
            return True
        if _unavailable:
            return False
        if not (MODEL_FILE.exists() and TOKENIZER_FILE.exists()) and not _download():
            _unavailable = True
            return False
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            _tokenizer = Tokenizer.from_file(str(TOKENIZER_FILE))
            _tokenizer.enable_padding()
            _tokenizer.enable_truncation(max_length=256)
            _session = ort.InferenceSession(str(MODEL_FILE), providers=["CPUExecutionProvider"])
            return True
        except Exception as error:  # onnxruntime/tokenizers missing, or a corrupt download
            logger.warning("Could not load the embedding model: %s", error)
            _unavailable = True
            return False


def _mean_pool(last_hidden_state, attention_mask) -> list[list[float]]:
    vectors = []
    for tokens, mask in zip(last_hidden_state, attention_mask):
        kept = [row for row, m in zip(tokens, mask) if m]
        width = len(tokens[0])
        if not kept:
            vectors.append([0.0] * width)
            continue
        summed = [sum(values) for values in zip(*kept)]
        vectors.append([v / len(kept) for v in summed])
    return vectors


def _normalize(vectors: list[list[float]]) -> list[list[float]]:
    """Also converts onnxruntime's numpy.float32 scalars to plain floats, since those are not
    JSON-serializable (and the JSON column is where vectors are stored)."""
    out = []
    for vector in vectors:
        norm = float(sum(v * v for v in vector)) ** 0.5
        out.append([float(v) / norm for v in vector] if norm > 0 else [float(v) for v in vector])
    return out


def embed(texts: list[str]) -> list[list[float]] | None:
    """One 384-dim, L2-normalized vector per text (cosine similarity = dot product), or None if
    the model could not be loaded. Empty strings still get a (zero) vector, one per input."""
    if not texts:
        return []
    if not _load():
        return None
    try:
        encodings = _tokenizer.encode_batch([t or "" for t in texts])
        input_ids = [e.ids for e in encodings]
        attention_mask = [e.attention_mask for e in encodings]
        token_type_ids = [[0] * len(ids) for ids in input_ids]
        outputs = _session.run(None, {"input_ids": input_ids, "attention_mask": attention_mask,
                                      "token_type_ids": token_type_ids})
        return _normalize(_mean_pool(outputs[0], attention_mask))
    except Exception as error:
        logger.warning("Embedding a batch of %d text(s) failed: %s", len(texts), error)
        return None


def cosine(a: list[float], b: list[float]) -> float:
    """Vectors from embed() are already L2-normalized, so this is just the dot product."""
    return sum(x * y for x, y in zip(a, b))


MIN_SIMILARITY = 0.3  # below this, all-MiniLM-L6-v2 pairs are unrelated more often than not
BATCH_SIZE = 64


def ensure_embeddings(db: Session) -> int:
    """Compute or refresh embeddings for every live item whose text changed since its last
    embedding (or that never had one). Returns how many were (re)computed. Local and cheap, so
    unlike AI enrichment this runs on every collection, not under a per-run budget.
    The caller owns the transaction; batches are flushed, never committed here."""
    from . import search_index

    items = db.query(models.Item).filter(models.Item.archived.is_(False)).all()
    existing = {e.item_id: e for e in db.query(models.ItemEmbedding).all()}
    stale = []
    for item in items:
        text = search_index.item_text(item) or item.name
        signature = hashlib.sha1(text.encode("utf-8")).hexdigest()
        row = existing.get(item.id)
        if row is None or row.signature != signature:
            stale.append((item.id, text, signature))

    computed = 0
    for start in range(0, len(stale), BATCH_SIZE):
        chunk = stale[start:start + BATCH_SIZE]
        vectors = embed([text for _, text, _ in chunk])
        if vectors is None:
            break  # model unavailable this run (e.g. no network yet); retry on the next one
        for (item_id, _, signature), vector in zip(chunk, vectors):
            row = existing.get(item_id)
            if row is None:
                row = models.ItemEmbedding(item_id=item_id)
                db.add(row)
                existing[item_id] = row
            row.vector = vector
            row.signature = signature
            row.computed_at = utcnow()
            computed += 1
        db.flush()
    return computed


def semantic_scores(db: Session, query: str, limit: int = 40) -> dict[int, float]:
    """item_id -> cosine similarity, for the most semantically similar live items to a free-form
    question, best first, above MIN_SIMILARITY. Empty when the model is unavailable. Used both to
    widen the candidate pool (its keys) and, unlike a plain keyword/FTS match, to score how good
    each match actually is (its values) -- candidacy alone does not rank an item any higher."""
    vectors = embed([query])
    if vectors is None:
        return {}
    query_vector = vectors[0]
    live_ids = {row[0] for row in db.query(models.Item.id).filter(models.Item.archived.is_(False))}
    scored = [(cosine(query_vector, e.vector), e.item_id) for e in db.query(models.ItemEmbedding).all()
              if e.item_id in live_ids]
    ranked = sorted((s for s in scored if s[0] >= MIN_SIMILARITY), reverse=True)[:limit]
    return {item_id: score for score, item_id in ranked}
