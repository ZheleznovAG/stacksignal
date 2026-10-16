"""Legacy one-time profile bootstrap. New installations start with no projects.

Existing migration markers and user profiles are preserved. PROJECT_PROFILES is
empty in the public distribution; tests supply fictional bootstrap fixtures.
"""

import json

from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import config, models

# Legacy local overrides for installations providing their own bootstrap profiles.
PRIVATE_PROFILES_PATH = config.DATA_DIR / "private_profiles.json"

PROJECT_PROFILES: list[dict] = []


def _load_private_overrides() -> dict:
    try:
        data = json.loads(PRIVATE_PROFILES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge_keywords(existing: list[str], incoming: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for kw in [*(existing or []), *incoming]:
        key = kw.lower()
        if key not in seen:
            seen.add(key)
            merged.append(kw)
    return merged


PROFILES_MIGRATION_NAME = "project_profiles_v1"


def apply_project_profiles(db: Session) -> dict[str, list[str] | bool]:
    """Runs the PROJECT_PROFILES migration exactly once per database.

    Safe to call on every startup: after the first successful run it's a fast no-op
    (one indexed lookup against `applied_migrations`), so it will never re-overwrite a
    user's edits or resurrect a project the user deleted.
    """
    already_applied = (
        db.query(models.AppliedMigration).filter_by(name=PROFILES_MIGRATION_NAME).one_or_none()
    )
    if already_applied is not None:
        return {"created": [], "updated": [], "skipped": True}

    result = _upsert_profiles(db)
    db.add(models.AppliedMigration(name=PROFILES_MIGRATION_NAME))
    db.commit()
    return {**result, "skipped": False}


def _upsert_profiles(db: Session) -> dict[str, list[str]]:
    """The actual merge-upsert, keyed by slug or by a known prior alias. Never deletes
    a project, including ones not in PROJECT_PROFILES. Idempotent — calling it more
    than once produces the same merged result — but only ever invoked once in
    practice, by apply_project_profiles() above.
    """
    created: list[str] = []
    updated: list[str] = []

    overrides = _load_private_overrides()

    for profile in PROJECT_PROFILES:
        override = overrides.get(profile["slug"])
        if isinstance(override, dict):
            profile = {**profile,
                       "description": override.get("description", profile["description"]),
                       "keywords": _merge_keywords(profile["keywords"], override.get("keywords") or [])}
        candidate_names = {profile["name"].lower(), *[a.lower() for a in profile["aliases"]]}
        existing = (
            db.query(models.Project)
            .filter(
                or_(
                    models.Project.slug == profile["slug"],
                    *[models.Project.name.ilike(n) for n in candidate_names],
                )
            )
            .one_or_none()
        )

        full_description = f"[{profile['lifecycle']}] {profile['description']}"

        if existing is None:
            db.add(
                models.Project(
                    name=profile["name"],
                    slug=profile["slug"],
                    description=full_description,
                    keywords=list(profile["keywords"]),
                )
            )
            created.append(profile["name"])
        else:
            # Normalizes a matched alias (e.g. a prior display name) onto the
            # canonical name/slug instead of leaving it as a separate duplicate.
            existing.name = profile["name"]
            existing.slug = profile["slug"]
            existing.description = full_description
            existing.keywords = _merge_keywords(existing.keywords, profile["keywords"])
            updated.append(profile["name"])

    db.commit()
    return {"created": created, "updated": updated}
