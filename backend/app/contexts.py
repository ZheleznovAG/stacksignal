"""Independent context policies. Discovery is opt-in; lifecycle controls attention, not searching."""

from sqlalchemy import and_

from . import models

ATTENDED_STATES = ("active", "watch", "incubate")


def discovery_allowed(project: models.Project) -> bool:
    return project.discovery_enabled is not False and project.lifecycle_status != "archived"


def discovery_filter():
    return and_(models.Project.discovery_enabled.is_(True), models.Project.lifecycle_status != "archived")


def attended(project: models.Project) -> bool:
    return (project.lifecycle_status or "active") in ATTENDED_STATES


def attended_filter():
    return models.Project.lifecycle_status.in_(ATTENDED_STATES)


def analysis_allowed(project: models.Project) -> bool:
    # Sleeping contexts retain their existing matching behavior; archived ones keep history only.
    return project.lifecycle_status != "archived"


def digest_limit(project: models.Project, *, normal: int = 3, limited: int = 1) -> int:
    if not attended(project):
        return 0
    return normal if project.lifecycle_status == "active" else limited


def profile_kind(project: models.Project) -> dict:
    # Preserve old project fingerprints: taxonomy only changes matching for reclassified contexts.
    return {"kind": project.kind} if project.kind and project.kind != "project" else {}


def apply_policy(project: models.Project, *, kind=None, lifecycle_status=None, discovery_enabled=None, tier=None) -> None:
    """Validate legacy/new API fields together before mutating anything."""
    if kind is not None and kind not in models.CONTEXT_KINDS:
        raise ValueError("Unknown context kind")
    if lifecycle_status is not None and lifecycle_status not in models.CONTEXT_STATES:
        raise ValueError("Unknown context lifecycle_status")
    if tier is not None:
        if tier not in models.PROJECT_TIERS:
            raise ValueError("tier must be active, watch or dormant")
        if (lifecycle_status is not None and lifecycle_status != tier or
                discovery_enabled is not None and discovery_enabled != (tier != "dormant")):
            raise ValueError("tier conflicts with lifecycle_status or discovery_enabled")
        lifecycle_status, discovery_enabled = tier, tier != "dormant"
    if kind is not None:
        project.kind = kind
    if lifecycle_status is not None:
        project.lifecycle_status = lifecycle_status
    if discovery_enabled is not None:
        project.discovery_enabled = discovery_enabled
