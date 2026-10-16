"""Explicitly installed pilot profiles. Never changes decisions or claims completed experiments."""
from . import models, needs

PROFILES = [
    {"slug": "chitalka", "match": "check a recitation", "title": "Offline Russian speech recognition on Android",
     "constraints": "Russian poetry, offline inference on Android; no mandatory cloud API.",
     "acceptance_criteria": "Measure word errors and missing-word detection on 10 recorded Russian recitations on a real Android device; record model size, latency and memory before choosing.",
     "search_terms": ["speech recognition", "speech-to-text", "asr", "russian", "android"]},
    {"slug": "softbody", "title": "Soft-body deformation and contact",
     "constraints": "A reproducible interactive soft-body example with collision/contact; distinguish research demonstrations from an integration-ready implementation.",
     "acceptance_criteria": "Run a minimal deformation/contact scene, document engine compatibility, stability under contact and measured frame time. Engine and performance target need user confirmation.",
     "search_terms": ["soft body", "soft-body", "deformable", "deformation", "collision", "contact"]},
    {"slug": "stacksignal", "match": "evaluate keyword/FTS", "title": "Evaluate retrieval quality on real questions",
     "constraints": "Russian and English questions; a local Python/SQLite catalogue; independently labelled expected answers rather than the system's own relevance labels.",
     "acceptance_criteria": "Label 20 real questions, measure recall@12 and false positives against the current keyword/FTS baseline; report misses separately for Russian and English.",
     "search_terms": ["retrieval", "rerank", "semantic search", "search evaluation", "information retrieval"]},
    {"slug": "stacksignal", "title": "Preserve decision evidence and experiment outcomes",
     "constraints": "Decisions belong to a specific context; keep user results separate from AI suggestions and preserve provenance when requirements change.",
     "acceptance_criteria": "Record 10 real candidate decisions with reasons and at least one measured experiment outcome; verify editing requirements preserves the history and makes affected assessments stale.",
     "search_terms": ["decision log", "decision tracking", "provenance", "experiment tracking", "feedback loop", "audit trail"]},
]


def seed(db):
    marker = "need_pilot_v1"
    if db.query(models.AppliedMigration).filter_by(name=marker).first():
        return []
    changed = []
    for profile in PROFILES:
        project = db.query(models.Project).filter_by(slug=profile["slug"]).one_or_none()
        if not project:
            continue
        rows = needs.links(db, project.id)
        link = next((l for l in rows if l.need.title == profile["title"] or
                     profile.get("match") and profile["match"] in l.need.title), None)
        if link is None:
            need = models.Need(title=profile["title"], origin="pilot")
            db.add(need)
            db.flush()
            link = models.ContextNeed(project_id=project.id, need_id=need.id)
            db.add(link)
            db.flush()
        link.pilot, link.priority = True, 3
        for field in ("constraints", "acceptance_criteria", "search_terms"):
            setattr(link, field, profile[field])
        needs.event(db, link, "pilot_setup", {"constraints": link.constraints, "acceptance_criteria": link.acceptance_criteria}, "system")
        needs.project_projection(db, project)
        changed.append({"project": project.slug, "context_need_id": link.id})
    db.add(models.AppliedMigration(name=marker))
    db.commit()
    return changed
