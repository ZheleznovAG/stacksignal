"""Stack watch: notice new versions of the packages your projects depend on.

The dependencies a profile scan recognised (pypi, npm) are looked up on their registries every run.
The first look only records the current version; after that a different latest version is an update:
"fastapi 0.140.0 -> 0.141.1, used by StackSignal". Dormant projects are not watched. Only PyPI and npm
have registries this can ask cheaply; other ecosystems (Maven, NuGet, Cargo...) are stored but not watched.
"""

import datetime
import logging
import re

from sqlalchemy.orm import Session, selectinload

from . import config, contexts, models
from .collectors import npm, pypi
from .dependencies import package_key
from .utils import utcnow

logger = logging.getLogger("stacksignal.stack_watch")

WATCHED = ("pypi", "npm")
RECENT_DAYS = 30


def _usage(db: Session, project_id: int | None = None) -> list[dict]:
    query = db.query(models.Project).options(selectinload(models.Project.dependencies))
    query = query.filter(models.Project.id == project_id) if project_id is not None else query.filter(contexts.attended_filter())
    out = []
    for project in query.order_by(models.Project.id):
        declarations = [{"ecosystem": d.ecosystem, "name": d.name, "package_key": d.package_key,
                         "declared": d.declared_spec, "resolved": d.resolved_version,
                         "manifest_path": d.manifest_path, "scope": d.scope, "purl": d.purl}
                        for d in project.dependencies]
        if not declarations:  # transitional support for legacy clients/imports
            declarations = [{"ecosystem": p.get("ecosystem", ""), "name": p.get("name", ""),
                             "declared": "", "resolved": "", "manifest_path": "", "scope": "runtime", "purl": ""}
                            for p in project.stack_packages or []]
        for dep in declarations:
            dep["ecosystem"] = dep["ecosystem"].strip().lower()
            dep["package_key"] = package_key(dep["ecosystem"], dep.get("package_key") or dep["name"])
            if dep["package_key"]:
                out.append({**dep, "project_id": project.id, "project_slug": project.slug, "projects": [project.name]})
    return out


def watched(db: Session) -> dict[tuple[str, str], list[str]]:
    """Registry identities used by non-dormant projects; declarations outrank the legacy cache."""
    found: dict[tuple[str, str], list[str]] = {}
    for dep in _usage(db):
        if dep["ecosystem"] in WATCHED:
            users = found.setdefault((dep["ecosystem"], dep["package_key"]), [])
            for name in dep["projects"]:
                if name not in users:
                    users.append(name)
    return found


def update_kind(resolved: str, latest: str) -> str | None:
    """Compare known stable numeric versions only. Constraints never stand in for a lockfile."""
    def parts(value):
        match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", value or "")
        return tuple(int(v or 0) for v in match.groups()) if match else None

    before, after = parts(resolved), parts(latest)
    if before is None or after is None:
        return None
    if after == before:
        return "current"
    if after < before:
        return "ahead"
    return next(kind for a, b, kind in zip(before, after, ("major", "minor", "patch")) if a != b)


def dependency_status(db: Session, *, project_id: int | None = None) -> list[dict]:
    """Each declaration alongside registry knowledge, including packages not checked yet."""
    known = _known_versions(db)
    out = []
    for dep in _usage(db, project_id):
        row = known.get((dep["ecosystem"], dep["package_key"]))
        latest = row.latest_version if row else ""
        out.append({**dep, "latest": latest, "update_kind": update_kind(dep["resolved"], latest),
                    "watched": dep["ecosystem"] in WATCHED,
                    "url": row.url if row else "",
                    "checked_at": row.checked_at.isoformat() if row else None})
    return out


def _known_versions(db: Session) -> dict[tuple[str, str], models.PackageVersion]:
    # Old PyPI rows used lowercasing only (Foo_Bar -> foo_bar). Reuse those rows and their
    # digest keys instead of resetting history when the identity becomes foo-bar.
    return {(r.ecosystem, package_key(r.ecosystem, r.name)): r for r in
            db.query(models.PackageVersion).order_by(models.PackageVersion.checked_at, models.PackageVersion.id)}


def _fetch(ecosystem: str, name: str, transport):
    return (pypi if ecosystem == "pypi" else npm).get_package(name, transport=transport)


def check(db: Session, *, limit: int | None = None, transport=None) -> dict:
    """Look up the watched packages (the ones checked longest ago first, up to `limit`) and record new versions."""
    limit = config.STACK_WATCH_PER_RUN if limit is None else limit
    stats = {"watched": 0, "checked": 0, "updated": 0, "errors": 0, "missing": 0}
    wanted = watched(db)
    stats["watched"] = len(wanted)
    known = _known_versions(db)
    order = sorted(wanted, key=lambda key: (known[key].checked_at if key in known else datetime.datetime.min))
    db.commit()  # finish preceding discovery writes before registry I/O
    now = utcnow()
    outcomes = []
    for ecosystem, name in order[:max(limit, 0)]:
        try:
            package = _fetch(ecosystem, name, transport)
        except Exception as error:
            stats["errors"] += 1
            logger.warning("Stack watch: %s %s failed: %s", ecosystem, name, error.__class__.__name__)
            continue
        if package is None:
            stats["missing"] += 1
            continue
        outcomes.append((ecosystem, name, package))
    # Apply only after every registry request; no autoflush can span the next fetch.
    known = _known_versions(db)
    for ecosystem, name, package in outcomes:
        version = (package.get("raw") or {}).get("version") or ""
        row = known.get((ecosystem, name))
        released = _date(package.get("source_updated_at"))
        if row is None:
            db.add(models.PackageVersion(ecosystem=ecosystem, name=name, latest_version=version, released_at=released,
                                         url=package.get("url") or "", checked_at=now))
        else:
            if version and version != row.latest_version:
                row.previous_version, row.latest_version, row.changed_at = row.latest_version, version, now
                stats["updated"] += 1
            row.released_at, row.checked_at, row.url = released or row.released_at, now, package.get("url") or row.url
        stats["checked"] += 1
    db.flush()
    return stats


def _date(value) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None) if value else None
    except ValueError:
        return None


def _major(version: str) -> str:
    match = re.match(r"v?(\d+)", version or "")
    return match.group(1) if match else ""


def updates(db: Session, *, days: int = RECENT_DAYS) -> list[dict]:
    """Versions that changed in the last `days` days for packages still in use, newest first."""
    users = watched(db)
    declarations: dict[tuple[str, str], list[dict]] = {}
    for dep in dependency_status(db):
        declarations.setdefault((dep["ecosystem"], dep["package_key"]), []).append(dep)
    cutoff = utcnow() - datetime.timedelta(days=days)
    rows = sorted((r for r in _known_versions(db).values() if r.changed_at and r.changed_at >= cutoff),
                  key=lambda r: r.changed_at, reverse=True)
    out = []
    for row in rows:
        identity = (row.ecosystem, package_key(row.ecosystem, row.name))
        projects = users.get(identity)
        if not projects:
            continue
        uses = declarations.get(identity, [])
        declared = {d["declared"] for d in uses}
        resolved = {d["resolved"] for d in uses}
        declared_value = next(iter(declared)) if len(declared) == 1 else ""
        resolved_value = next(iter(resolved)) if len(resolved) == 1 else ""
        out.append({"ecosystem": row.ecosystem, "name": row.name, "previous": row.previous_version,
                    "declared": declared_value, "resolved": resolved_value, "dependencies": uses,
                    "update_kind": update_kind(resolved_value, row.latest_version),
                    "latest": row.latest_version, "released_at": row.released_at.isoformat() if row.released_at else None,
                    "changed_at": row.changed_at.isoformat(), "url": row.url, "projects": projects,
                    "major": bool(_major(row.previous_version) and _major(row.previous_version) != _major(row.latest_version)),
                    "key": f"{row.ecosystem}:{row.name}@{row.latest_version}"})
    return out
