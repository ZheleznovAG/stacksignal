"""Fills the browser tests' scratch database with a fictional project and synthetic analyses,
so Today, the project workspace and the item drawer have something to show.
Run as a script with STACKSIGNAL_DATABASE_URL pointing at the scratch file."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402

ITEMS = [
    ("whisper.cpp", "ggml-org", "Port of Whisper speech-to-text in C/C++ for local inference", 0.95),
    ("sherpa-onnx", "k2-fsa", "Offline speech recognition and TTS for Android with ONNX", 0.9),
    ("tesseract", "tesseract-ocr", "OCR engine for printed text", 0.7),
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        chitalka = models.Project(name="Читалка", slug="chitalka",
                                 description="Demo Android reader with offline OCR and speech import",
                                 keywords=["ocr", "speech-to-text", "Android"])
        db.add(chitalka)
        db.flush()
        ocr = models.Capability(name="speech to text", slug="speech-to-text")
        db.add(ocr)
        db.flush()
        for name, owner, description, score in ITEMS:
            item = models.Item(source="github", external_id=f"{owner}/{name}", name=name, owner=owner,
                               url=f"https://github.com/{owner}/{name}", description=description, topics=[],
                               stars=1000, forks=100, momentum_score=0.6, maturity_score=0.8,
                               llm_summary=description, raw={})
            db.add(item)
            db.flush()
            db.add_all([
                models.ItemProjectRelevance(item_id=item.id, project_id=chitalka.id, score=score, base_score=score,
                                            rationale=f"{name} fits Читалка"),
                models.ItemAnalysisState(item_id=item.id, provenance={"mode": "ai"}),
                models.ItemCapability(item_id=item.id, capability_id=ocr.id, confidence=0.9),
                models.StatusHistory(item_id=item.id, status=models.Status.NEW, note="Discovered"),
            ])
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
