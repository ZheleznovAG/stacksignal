import datetime
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    case,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils import utcnow


def now() -> datetime.datetime:
    return utcnow()


PROJECT_TIERS = ("active", "watch", "dormant")
CONTEXT_KINDS = ("project", "lab", "infrastructure", "experiment", "goal", "personal_system", "reference_project")
CONTEXT_STATES = ("active", "watch", "dormant", "archived", "incubate")


class Status(str, enum.Enum):
    NEW = "new"
    WATCH = "watch"
    ASSESS = "assess"
    TRIAL = "trial"
    ADOPT = "adopt"
    HOLD = "hold"
    IGNORE = "ignore"


class Source(str, enum.Enum):
    GITHUB = "github"
    HUGGINGFACE = "huggingface"
    ARXIV = "arxiv"
    NPM = "npm"
    PYPI = "pypi"


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("kind IN ('project','lab','infrastructure','experiment','goal','personal_system','reference_project')",
                        name="ck_project_kind"),
        CheckConstraint("lifecycle_status IN ('active','watch','dormant','archived','incubate')", name="ck_project_lifecycle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(30), default="project", server_default="project")
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    discovery_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # From scanning the project's real folder (profile_import.py): the technologies it already uses and the
    # capabilities it lacks or plans. The AI is told about both, so it stops pitching what you have.
    stack: Mapped[list] = mapped_column(JSON, default=list)
    needs: Mapped[list] = mapped_column(JSON, default=list)
    # [{"name", "ecosystem"}] for the dependencies the scan recognised (pypi, npm, ...): what to watch for new versions.
    stack_packages: Mapped[list] = mapped_column(JSON, default=list)
    stack_path: Mapped[str] = mapped_column(String(500), default="")
    stack_scanned_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)

    relevances: Mapped[list["ItemProjectRelevance"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    dependencies: Mapped[list["ProjectDependency"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectDependency.id"
    )

    @hybrid_property
    def tier(self) -> str:
        """Legacy API/constructor compatibility, derived from lifecycle rather than stored twice."""
        state = self.lifecycle_status or "active"
        return "active" if state == "active" else "watch" if state in ("watch", "incubate") else "dormant"

    @tier.setter
    def tier(self, value: str) -> None:
        if value not in PROJECT_TIERS:
            raise ValueError("tier must be active, watch or dormant")
        self.lifecycle_status = value
        self.discovery_enabled = value != "dormant"

    @tier.expression
    def tier(cls):
        return case((cls.lifecycle_status == "active", "active"),
                    (cls.lifecycle_status.in_(("watch", "incubate")), "watch"), else_="dormant")


class ProjectTechnicalModel(Base):
    """Latest structured understanding of a project produced from its profile/stack.

    This is deliberately a snapshot, not a second authoritative project specification. The user-authored
    Project and ContextNeed rows remain the source of truth; this row explains how StackSignal currently
    understands goals, subsystems and gaps and carries a source fingerprint so staleness is explicit.
    """
    __tablename__ = "project_technical_models"
    __table_args__ = (UniqueConstraint("project_id", name="uq_project_technical_model_project"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(80), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    goals: Mapped[list] = mapped_column(JSON, default=list)
    subsystems: Mapped[list] = mapped_column(JSON, default=list)
    gaps: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)

    project: Mapped["Project"] = relationship()


class ProjectDependency(Base):
    __tablename__ = "project_dependencies"
    __table_args__ = (UniqueConstraint("project_id", "ecosystem", "package_key", "manifest_path", "scope",
                                      "declared_spec", name="uq_project_dependency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    ecosystem: Mapped[str] = mapped_column(String(30))
    package_key: Mapped[str] = mapped_column(String(400))
    name: Mapped[str] = mapped_column(String(300))
    declared_spec: Mapped[str] = mapped_column(String(300), default="")
    resolved_version: Mapped[str] = mapped_column(String(120), default="")
    manifest_path: Mapped[str] = mapped_column(String(500), default="")
    scope: Mapped[str] = mapped_column(String(30), default="runtime")
    purl: Mapped[str] = mapped_column(String(600), default="")

    project: Mapped["Project"] = relationship(back_populates="dependencies")


class Capability(Base):
    __tablename__ = "capabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)

    item_links: Mapped[list["ItemCapability"]] = relationship(
        back_populates="capability", cascade="all, delete-orphan"
    )


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(Enum(Source), index=True)
    external_id: Mapped[str] = mapped_column(String(300), index=True)
    name: Mapped[str] = mapped_column(String(300))
    owner: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    topics: Mapped[list] = mapped_column(JSON, default=list)

    stars: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)

    # Extrapolated weekly-equivalent growth rate (stars_per_day * 7 for GitHub,
    # attention_per_day * 7 for Hugging Face), derived from real elapsed time between
    # ItemSnapshot rows — NOT simply "stars now minus stars at the last collection run".
    # 0.0 until at least one snapshot older than ~12h exists for this item.
    stars_delta_7d: Mapped[float] = mapped_column(Float, default=0.0)

    source_created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    source_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    first_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    last_enriched_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    llm_summary: Mapped[str] = mapped_column(Text, default="")
    why_interesting: Mapped[str] = mapped_column(Text, default="")

    momentum_score: Mapped[float] = mapped_column(Float, default=0.0)
    maturity_score: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[str] = mapped_column(Enum(Status), default=Status.NEW, index=True)
    status_updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)

    # Archived items stay in the database (nothing is deleted) but are hidden from every list,
    # the chat, capabilities and the digest. They come back automatically if a later collection
    # finds them passing the quality gate, or when the user engages with them.
    releases_checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Entity resolution (app/entities.py): a package or paper that is really the same technology
    # as one GitHub repository already tracked, found through the registry's own repository URL
    # -- never by fuzzy name matching, which is precise but only ever links what a source itself
    # already says is the same thing.
    canonical_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), nullable=True, index=True)
    canonical_item: Mapped["Item | None"] = relationship(remote_side=[id], back_populates="same_as_items")
    same_as_items: Mapped[list["Item"]] = relationship(back_populates="canonical_item")

    capability_links: Mapped[list["ItemCapability"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    relevances: Mapped[list["ItemProjectRelevance"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    releases: Mapped[list["ItemRelease"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="ItemRelease.published_at.desc()")
    decisions: Mapped[list["ItemProjectDecision"]] = relationship(
        back_populates="item", cascade="all, delete-orphan")
    status_history: Mapped[list["StatusHistory"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="StatusHistory.created_at"
    )
    feedback: Mapped[list["ItemFeedback"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="ItemFeedback.id"
    )


class ItemSnapshot(Base):
    """A point-in-time reading of an item's popularity metrics, recorded on every
    collection run regardless of how often that run happens. Real momentum (attention
    gained per day) is computed by diffing against the snapshot closest to N days ago,
    not by diffing against "whatever the value was at the previous run"."""

    __tablename__ = "item_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    captured_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now, index=True)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    downloads: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)


class ItemCapability(Base):
    __tablename__ = "item_capabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    capability_id: Mapped[int] = mapped_column(ForeignKey("capabilities.id"), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    item: Mapped["Item"] = relationship(back_populates="capability_links")
    capability: Mapped["Capability"] = relationship(back_populates="item_links")


class ItemProjectRelevance(Base):
    __tablename__ = "item_project_relevance"
    __table_args__ = (UniqueConstraint("item_id", "project_id", name="uq_item_project"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1, after any user vote
    rationale: Mapped[str] = mapped_column(Text, default="")
    # The analysis' own score before a user vote overrode it, so clearing a vote can restore it.
    base_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    item: Mapped["Item"] = relationship(back_populates="relevances")
    project: Mapped["Project"] = relationship(back_populates="relevances")


class ItemProjectDecision(Base):
    """What the user decided about an item *for one project*, with the goal and the outcome."""

    __tablename__ = "item_project_decisions"
    __table_args__ = (UniqueConstraint("item_id", "project_id", name="uq_item_project_decision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(Enum(Status))
    goal: Mapped[str] = mapped_column(Text, default="")      # what to try / check
    verdict: Mapped[str] = mapped_column(Text, default="")   # what came out, or why not
    criteria: Mapped[str] = mapped_column(Text, default="")  # what would count as success
    revisit_when: Mapped[str] = mapped_column(Text, default="")  # what change would make you look again
    decided_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)  # when the status last changed
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # the item as it was then (see revisit.snapshot)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)

    item: Mapped["Item"] = relationship(back_populates="decisions")
    project: Mapped["Project"] = relationship()


class StatusHistory(Base):
    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)  # None = global status
    status: Mapped[str] = mapped_column(Enum(Status))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)

    item: Mapped["Item"] = relationship(back_populates="status_history")


class Opportunity(Base):
    """A cross-item insight that persists between runs. `item_ids` is its current evidence, `trend`
    the evidence count at each run it was seen; `state` is active / faded / dismissed / done."""

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    project_slug: Mapped[str] = mapped_column(String(200), default="")
    item_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    first_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(20), default="active")
    origin: Mapped[str] = mapped_column(String(20), default="ai")  # "ai" | "heuristic"
    trend: Mapped[list] = mapped_column(JSON, default=list)
    digested_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)  # sent in the digest


class ItemRelease(Base):
    """A published release of a GitHub repository you track (see release_watch.py)."""

    __tablename__ = "item_releases"
    __table_args__ = (UniqueConstraint("item_id", "tag", name="uq_item_release"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    tag: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    prerelease: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    first_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)

    item: Mapped["Item"] = relationship(back_populates="releases")


class PackageVersion(Base):
    """The newest known version of a package one of your projects depends on (see stack_watch.py)."""

    __tablename__ = "package_versions"
    __table_args__ = (UniqueConstraint("ecosystem", "name", name="uq_package_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ecosystem: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    latest_version: Mapped[str] = mapped_column(String(80), default="")
    previous_version: Mapped[str] = mapped_column(String(80), default="")
    released_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    changed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)  # when latest last changed
    checked_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    url: Mapped[str] = mapped_column(String(500), default="")


class AppliedMigration(Base):
    """Tracks one-time data migrations (e.g. the initial project-profile bootstrap) so
    they run exactly once against a given database and never re-apply on a later
    startup — which would otherwise silently overwrite user edits or resurrect
    user-deleted rows every time the app restarts."""

    __tablename__ = "applied_migrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    applied_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)


class CollectionRun(Base):
    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_enriched: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    # running -> completed (all stages finished), partial (errors with saved checkpoints) or
    # failed (fatal error before the first useful checkpoint). The run itself is always kept.
    status: Mapped[str] = mapped_column(String(20), default="completed")
    # [{"name", "started_at", "duration_s", "status": "ok"|"error", "error"?}] per top-level phase
    # (collect, discovery, gate_and_score, enrich, opportunities, capabilities, embeddings,
    # housekeeping): answers "which stage failed, how long it took, and why the digest is empty"
    # without instrumenting anything external.
    stages: Mapped[list] = mapped_column(JSON, default=list)


class AIAnalysis(Base):
    """Content-addressed results and failed attempts; no credentials in this table."""
    __tablename__ = "ai_analyses"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), nullable=True, index=True)
    task: Mapped[str] = mapped_column(String(80))
    profile: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(80))
    routing_version: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30))
    provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    response_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    request_key: Mapped[str] = mapped_column(String(100))
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | str | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)


class ItemAnalysisState(Base):
    __tablename__ = "item_analysis_state"
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)


class ItemEmbedding(Base):
    """A local sentence embedding (see app/embeddings.py), used by the chat to find items a
    keyword search misses because the question and the item use different words for the same
    thing. `signature` is compared against the item's current text on each run so a changed
    description gets a fresh vector instead of a stale one."""

    __tablename__ = "item_embeddings"
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    vector: Mapped[list] = mapped_column(JSON)
    signature: Mapped[str] = mapped_column(String(64), default="")
    computed_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)


class ItemFeedback(Base):
    """The user's own judgments, kept apart from anything the analysis produces so they
    survive re-analysis. A "vote" row says useful (1) / not relevant (-1) / cleared (0) for one
    project (latest vote per project wins); a "note" row is free text about the item."""

    __tablename__ = "item_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # "vote" | "note"
    project_slug: Mapped[str] = mapped_column(String(200), default="")
    verdict: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)

    item: Mapped["Item"] = relationship(back_populates="feedback")


class DigestLog(Base):
    """One attempt to send the Telegram digest. `item_ids` / `opportunity_titles` are what a
    successful digest contained, so the next one only carries what is new."""

    __tablename__ = "digest_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # "scheduled" | "manual"
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(String(120), default="")
    messages: Mapped[int] = mapped_column(Integer, default=0)
    item_ids: Mapped[list] = mapped_column(JSON, default=list)
    opportunity_titles: Mapped[list] = mapped_column(JSON, default=list)
    stack_update_keys: Mapped[list] = mapped_column(JSON, default=list)  # "pypi:fastapi@0.141.1" already sent
    release_keys: Mapped[list] = mapped_column(JSON, default=list)  # "owner/repo@v1.2.0" already sent
    revisit_keys: Mapped[list] = mapped_column(JSON, default=list)  # "<decision id>:<evidence signature>" already sent


class SearchQuery(Base):
    """A saved search that collection runs against GitHub or Hugging Face to find items for a
    project (project_slug "" = a global query). Auto/AI queries are generated from the project's
    profile; "user" queries are the user's own. Any of them can be switched off or removed."""

    __tablename__ = "search_queries"
    __table_args__ = (UniqueConstraint("project_slug", "source", "text", name="uq_query"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_slug: Mapped[str] = mapped_column(String(200), default="", index=True)
    source: Mapped[str] = mapped_column(String(20))  # "github" | "huggingface" | "arxiv" | "npm"
    text: Mapped[str] = mapped_column(String(300))
    origin: Mapped[str] = mapped_column(String(10), default="auto")  # "auto" | "ai" | "user"
    profile_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    basis: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["need:persistent outbox"] or ["profile"]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_found: Mapped[int] = mapped_column(Integer, default=0)
    last_new: Mapped[int] = mapped_column(Integer, default=0)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    total_new: Mapped[int] = mapped_column(Integer, default=0)
    empty_streak: Mapped[int] = mapped_column(Integer, default=0)  # consecutive runs that found nothing new


class ItemDiscovery(Base):
    """Why an item is in the database: which project's search query found it (first time)."""

    __tablename__ = "item_discoveries"
    __table_args__ = (UniqueConstraint("item_id", "project_slug", name="uq_item_discovery"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    project_slug: Mapped[str] = mapped_column(String(200), default="")
    query_text: Mapped[str] = mapped_column(String(300), default="")
    found_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)


class Author(Base):
    """A GitHub account worth following as a source of technologies (app/authors.py).

    Where it came from is kept as plain facts -- you follow it, you starred its repositories, you added
    it yourself -- separately from `status`, which says whether it is still worth watching."""

    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(100))
    login_key: Mapped[str] = mapped_column(String(100), unique=True)  # GitHub logins are case-insensitive
    github_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="user")  # user | organization
    status: Mapped[str] = mapped_column(String(20), default="watch", index=True)  # watch | ignore

    followed: Mapped[bool] = mapped_column(Boolean, default=False)
    starred_repos: Mapped[int] = mapped_column(Integer, default=0)
    last_starred_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    added_manually: Mapped[bool] = mapped_column(Boolean, default=False)

    first_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    repos_checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class AuthorRepo(Base):
    """A watched author's repository as last seen on their profile -- metadata only. Most never become
    items; this remembers what was already seen and what was already put on the radar."""

    __tablename__ = "author_repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(300), unique=True)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    fork: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    pushed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    promoted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class Need(Base):
    __tablename__ = "needs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(600))
    origin: Mapped[str] = mapped_column(String(30), default="user")


class ContextNeed(Base):
    __tablename__ = "context_need_links"
    __table_args__ = (UniqueConstraint("project_id", "need_id", name="uq_context_need"),
                     CheckConstraint("state IN ('open','evaluating','satisfied','deferred','dropped')", name="ck_need_state"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    need_id: Mapped[int] = mapped_column(ForeignKey("needs.id"))
    priority: Mapped[int] = mapped_column(Integer, default=2)
    state: Mapped[str] = mapped_column(String(20), default="open")
    constraints: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    search_terms: Mapped[list] = mapped_column(JSON, default=list)
    pilot: Mapped[bool] = mapped_column(Boolean, default=False)
    result_note: Mapped[str] = mapped_column(Text, default="")
    result_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    confirmed_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    need: Mapped["Need"] = relationship()
    project: Mapped["Project"] = relationship()


class NeedDependency(Base):
    """A project-local prerequisite edge: prerequisite -> dependent.

    The edge belongs to the project even though both endpoints are context_need_links; keeping
    project_id explicit makes filtering/indexing cheap and lets application validation reject
    cross-project edges. ``origin`` separates AI-maintained graph edges from future user edits.
    """
    __tablename__ = "need_dependencies"
    __table_args__ = (
        UniqueConstraint("project_id", "prerequisite_id", "dependent_id", name="uq_need_dependency"),
        CheckConstraint("prerequisite_id != dependent_id", name="ck_need_dependency_not_self"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    prerequisite_id: Mapped[int] = mapped_column(
        ForeignKey("context_need_links.id", ondelete="CASCADE"), index=True)
    dependent_id: Mapped[int] = mapped_column(
        ForeignKey("context_need_links.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(30), default="project_ai")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)


class NeedAssessment(Base):
    __tablename__ = "item_need_relevance"
    __table_args__ = (UniqueConstraint("item_id", "context_need_id", name="uq_item_context_need"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    context_need_id: Mapped[int] = mapped_column(ForeignKey("context_need_links.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    constraint_status: Mapped[str] = mapped_column(String(20), default="unknown")
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    assessed_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)


class NeedEvent(Base):
    __tablename__ = "need_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    context_need_id: Mapped[int] = mapped_column(ForeignKey("context_need_links.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(20), default="user")
    action: Mapped[str] = mapped_column(String(30))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)


class NeedExperiment(Base):
    """A frozen agent brief and the separately recorded outcome of an actual check."""
    __tablename__ = "need_experiments"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_need_experiment_active_key"),
        CheckConstraint("status IN ('draft','running','completed','cancelled')", name="ck_experiment_status"),
        CheckConstraint("outcome IN ('','useful','not_useful','inconclusive')", name="ck_experiment_outcome"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(Integer, default=1)
    context_need_id: Mapped[int] = mapped_column(ForeignKey("context_need_links.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"), nullable=True, index=True)
    active_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    brief: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    outcome: Mapped[str] = mapped_column(String(20), default="")
    result_note: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    need_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=now)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    __mapper_args__ = {"version_id_col": version_id}
