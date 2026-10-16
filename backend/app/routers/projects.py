from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload

from .. import (config, contexts, models, needs, profile_import, project_directory,
                project_intelligence, project_model, queries, schemas)
from ..ai_analysis import AIContext
from ..ai_client import AIError
from ..db import get_db
from ..dependencies import package_key
from ..serializers import ser_project
from ..utils import slugify, unique_slug, utcnow

router = APIRouter()


@router.get("/api/projects")
def list_projects(db: Session = Depends(get_db)):
    return [ser_project(p) for p in db.query(models.Project).options(selectinload(models.Project.dependencies))
            .order_by(models.Project.name).all()]


@router.get("/api/projects/scan-directory")
def project_directory_options():
    return {"path": project_directory.default_path()}


@router.post("/api/projects/scan-directory")
def scan_project_directory(body: schemas.DirectoryScanIn, db: Session = Depends(get_db)):
    try:
        return project_directory.scan(db, body.path)
    except profile_import.ScanError as error:
        raise HTTPException(400, error.code)


@router.post("/api/projects/import-directory")
def import_project_directory(body: schemas.DirectoryImportIn, db: Session = Depends(get_db)):
    """Import only reviewed children, in one transaction. Recheck duplicates at save time."""
    try:
        root = project_directory.directory(body.path)
        paths = [project_directory.child_folder(root, p.path) for p in body.projects]
    except profile_import.ScanError as error:
        raise HTTPException(400, error.code)
    projects = db.query(models.Project).all()
    created, skipped = [], []
    for candidate, path in zip(body.projects, paths):
        existing = project_directory.existing_project(projects, str(path), candidate.name)
        if existing:
            skipped.append({"path": str(path), "project_id": existing.id, "name": existing.name})
            continue
        project = _create_project(db, candidate)
        _apply_scan(db, project, schemas.ApplyScanIn(
            path=str(path), stack=candidate.stack, packages=candidate.packages,
            needs=candidate.needs or []))
        projects.append(project)
        created.append(project)
    db.commit()
    return {"created": [ser_project(p) for p in created], "skipped": skipped}




@router.get("/api/projects/{project_id}/model")
def get_project_model(project_id: int, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    model = project_model.serialise(db, project)
    if model is None:
        return {"project_id": project.id, "project_slug": project.slug, "project_name": project.name,
                "available": False, "stale": True, "summary": "", "goals": [], "subsystems": [],
                "gaps": [], "risks": [], "provenance": {}, "updated_at": None}
    model["available"] = True
    return model


@router.post("/api/projects/{project_id}/infer-needs")
def infer_project_needs(project_id: int, db: Session = Depends(get_db)):
    """Understand the project from its profile and persist a structured need portfolio.

    This is project-first personalization: no item votes are required. Hand-written/imported needs remain
    authoritative and are never deleted or rewritten by the inference pass.
    """
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    if len((project.description or "").strip()) < 40:
        raise HTTPException(400, "project_description_too_short")
    if not config.LLM_ENABLED:
        raise HTTPException(503, "ai_disabled")
    try:
        proposal = project_intelligence.infer(db, project, context=AIContext(max_calls=1))
    except AIError as error:
        db.rollback()
        raise HTTPException(503, error.code)
    applied = project_intelligence.apply(db, project, proposal)
    # Project.needs changed, so heuristic discovery queries should follow the new profile immediately.
    active = [p for p in db.query(models.Project).all() if contexts.discovery_allowed(p)]
    applied["queries_added"] = queries.ensure_default_queries(db, active)
    db.commit()
    applied["technical_model"] = project_model.serialise(db, project)
    return applied


@router.post("/api/projects/{project_id}/scan")
def scan_project_folder(project_id: int, body: schemas.ScanIn, db: Session = Depends(get_db)):
    """Read a project's real folder (manifests + README only) and propose its stack and, with the AI, its
    description, keywords and needs. Nothing is saved until it is applied."""
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    try:
        scan = profile_import.scan_folder(body.path)
    except profile_import.ScanError as error:
        raise HTTPException(400, error.code)
    proposal, ai_error = profile_import.heuristic_proposal(scan, project), ""
    if body.ai and config.LLM_ENABLED:
        try:
            proposal = profile_import.ai_proposal(db, scan, project, context=AIContext(max_calls=1))
            db.commit()
        except AIError as error:
            db.commit()
            ai_error = error.code
    return {"path": scan["path"], "files": scan["files"], "dependencies": scan["dependencies"],
            "proposal": proposal, "ai_error": ai_error}


@router.post("/api/projects/{project_id}/apply-scan")
def apply_project_scan(project_id: int, body: schemas.ApplyScanIn, db: Session = Depends(get_db)):
    """Save a reviewed proposal: the stack and needs are stored, keywords and description only if given."""
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    _apply_scan(db, project, body)
    db.commit()
    return ser_project(project)


def _apply_scan(db: Session, project: models.Project, body: schemas.ApplyScanIn) -> None:
    clean = lambda values: list(dict.fromkeys(v.strip() for v in values if v and v.strip()))[:80]
    project.stack = clean(body.stack)
    needs.sync_legacy(db, project, clean(body.needs))
    project.dependencies.clear()
    db.flush()  # remove old identities before inserting declarations from the new scan
    seen = set()
    legacy = {}
    for package in body.packages:
        data = {key: value.strip() for key, value in package.model_dump().items()}
        data["ecosystem"] = data["ecosystem"].lower()
        data["package_key"] = package_key(data["ecosystem"], data["package_key"] or data["name"])
        identity = tuple(data[k] for k in ("ecosystem", "package_key", "manifest_path", "scope", "declared_spec"))
        if not data["name"] or not data["package_key"] or identity in seen:
            continue
        seen.add(identity)
        project.dependencies.append(models.ProjectDependency(**data))
        legacy[(data["ecosystem"], data["package_key"])] = {"name": data["package_key"], "ecosystem": data["ecosystem"]}
    project.stack_packages = list(legacy.values())
    project.stack_path = body.path.strip()[:500]
    project.stack_scanned_at = utcnow()
    if body.keywords is not None:
        project.keywords = clean(body.keywords)
    if body.description is not None:
        project.description = body.description.strip()


def _apply_policy(project: models.Project, body: schemas.ProjectIn) -> None:
    try:
        contexts.apply_policy(project, **{key: getattr(body, key) for key in
                              ("tier", "kind", "lifecycle_status", "discovery_enabled")})
    except ValueError as error:
        raise HTTPException(400, str(error))


@router.post("/api/projects")
def create_project(body: schemas.ProjectIn, db: Session = Depends(get_db)):
    project = _create_project(db, body)
    db.commit()
    return ser_project(project)


def _create_project(db: Session, body: schemas.ProjectIn) -> models.Project:
    # SQLite's lower() only folds ASCII, so the comparison runs here for Cyrillic names too.
    if body.name.strip().casefold() in {name.strip().casefold() for (name,) in db.query(models.Project.name)}:
        raise HTTPException(400, "A project with this name already exists")
    # Names that differ only in punctuation ("Мой проект" / "Мой проект!") share a slug; the suffix keeps them apart.
    base = slugify(body.name)
    slug = unique_slug("project" if base == "item" else base,
                       lambda s: db.query(models.Project.id).filter_by(slug=s).first() is not None)
    project = models.Project(name=body.name, slug=slug, description=body.description, keywords=body.keywords,
                             kind="project", lifecycle_status="active", discovery_enabled=True,
                             needs=list(dict.fromkeys(n.strip() for n in (body.needs or []) if n.strip()))[:80])
    if body.lifecycle_status in ("dormant", "archived") and body.discovery_enabled is None:
        project.discovery_enabled = False
    _apply_policy(project, body)
    db.add(project)
    db.flush()
    needs.sync_legacy(db, project, project.needs, actor="user")
    return project


@router.put("/api/projects/{project_id}")
def update_project(project_id: int, body: schemas.ProjectIn, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    other_names = db.query(models.Project.name).filter(models.Project.id != project_id)
    if body.name.strip().casefold() in {name.strip().casefold() for (name,) in other_names}:
        raise HTTPException(400, "A project with this name already exists")
    _apply_policy(project, body)
    project.name = body.name
    project.description = body.description
    project.keywords = body.keywords
    if body.needs is not None:
        needs.sync_legacy(db, project, body.needs, actor="user")
    db.commit()
    return ser_project(project)


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    db.query(models.SearchQuery).filter_by(project_slug=project.slug).delete()  # its searches go with it
    db.query(models.ItemProjectDecision).filter_by(project_id=project.id).delete()
    slug = project.slug
    db.query(models.ItemFeedback).filter_by(kind="vote", project_slug=slug).delete()
    db.query(models.Opportunity).filter_by(project_slug=slug).delete()
    db.query(models.ItemDiscovery).filter_by(project_slug=slug).delete()
    db.query(models.StatusHistory).filter_by(project_id=project.id).delete()
    db.delete(project)
    db.commit()
    return {"ok": True}
