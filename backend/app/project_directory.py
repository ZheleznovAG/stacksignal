"""Discover project folders without importing code or changing existing profiles."""

import os
import re
from pathlib import Path

from . import config, models, profile_import

MAX_FOLDERS = 100


def default_path() -> str:
    return str(config.ROOT_DIR.parent)


def directory(path: str) -> Path:
    try:
        root = Path(path or default_path()).expanduser()
        if not root.is_dir():
            raise profile_import.ScanError("not_a_folder")
        return root.resolve()
    except OSError:
        raise profile_import.ScanError("folder_unreadable") from None


def path_key(path: str) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve())) if path else ""


def _name_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())


def existing_project(projects: list[models.Project], path: str, name: str) -> models.Project | None:
    key = path_key(path)
    for project in projects:
        if project.stack_path and path_key(project.stack_path) == key:
            return project
    names = {_name_key(name), _name_key(Path(path).name)} - {""}
    return next((p for p in projects if names & {_name_key(p.name), _name_key(p.slug)}), None)


def child_folder(root: Path, path: str) -> Path:
    child = Path(path).expanduser()
    if (child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()) or not child.is_dir()
            or child.resolve().parent != root):
        raise profile_import.ScanError("not_a_project_subfolder")
    return child.resolve()


def scan(db, path: str) -> dict:
    root = directory(path)
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        raise profile_import.ScanError("folder_unreadable") from None
    projects = db.query(models.Project).all()
    found, skipped = [], []
    folders = [p for p in children if not p.name.startswith(".") and p.is_dir()
               and p.name not in profile_import._SKIP_DIRS]
    for child in folders[:MAX_FOLDERS]:
        try:
            child = child_folder(root, str(child))
            result = profile_import.scan_folder(str(child))
        except (OSError, profile_import.ScanError):
            skipped.append({"name": child.name, "reason": "folder_unreadable"})
            continue
        if not result["files"]:
            skipped.append({"name": child.name, "reason": "no_project_files"})
            continue
        existing = existing_project(projects, str(child), child.name)
        proposal = profile_import.heuristic_proposal(
            result, models.Project(name=child.name, slug="", description="", keywords=[]))
        found.append({
            "path": str(child), "name": child.name[:200], "files": result["files"],
            "dependencies": result["dependencies"], "proposal": proposal,
            "existing_project": None if existing is None else {
                "id": existing.id, "slug": existing.slug, "name": existing.name},
        })
    return {"path": str(root), "projects": found, "skipped": skipped,
            "truncated": len(folders) > MAX_FOLDERS, "limit": MAX_FOLDERS}
