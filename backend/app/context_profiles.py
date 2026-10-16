"""One-time reclassification of known profiles, preserving identity and user-written content."""

from sqlalchemy.orm import Session

from . import models

MIGRATION_NAME = "context_profiles_v1"

# Display names may change, slugs never do. Custom display names are left intact.
PROFILES: dict[str, dict] = {}


def preview(db: Session) -> list[dict]:
    rows = db.query(models.Project).order_by(models.Project.id).all()
    names = {p.name.strip().casefold() for p in rows}
    changes = []
    for project in rows:
        profile = PROFILES.get(project.slug)
        if not profile or project.kind != "project":
            continue
        wanted = {key: value for key, value in profile.items() if key != "rename"}
        rename = profile.get("rename")
        if rename and project.name == rename[0] and rename[1].casefold() not in names:
            wanted["name"] = rename[1]
            names.add(rename[1].casefold())
        fields = {key: {"before": getattr(project, key), "after": value}
                  for key, value in wanted.items() if getattr(project, key) != value}
        if fields:
            changes.append({"id": project.id, "slug": project.slug, "fields": fields})
    return changes


def apply(db: Session) -> list[dict]:
    if db.query(models.AppliedMigration).filter_by(name=MIGRATION_NAME).first():
        return []
    changes = preview(db)
    for change in changes:
        project = db.get(models.Project, change["id"])
        for key, values in change["fields"].items():
            setattr(project, key, values["after"])
    db.add(models.AppliedMigration(name=MIGRATION_NAME))
    db.commit()
    return changes
