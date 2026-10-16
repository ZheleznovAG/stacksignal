from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

# Size limits: generous for a person typing, but a stray or hostile request cannot push megabytes
# into the database or into an AI prompt. Short ones match the column sizes in models.py.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Short = Annotated[str, StringConstraints(max_length=300)]
Url = Annotated[str, StringConstraints(max_length=500)]
Text = Annotated[str, StringConstraints(max_length=10_000)]
Terms = Annotated[list[Short], Field(max_length=200)]


class ProjectIn(BaseModel):
    name: Name
    description: Text = ""
    keywords: Terms = Field(default_factory=list)
    tier: str | None = None  # None keeps the current tier (new projects are active)
    kind: str | None = None
    lifecycle_status: str | None = None
    discovery_enabled: bool | None = None
    needs: Terms | None = None  # None keeps the current needs


class StatusIn(BaseModel):
    status: Short
    note: Text = ""


class ScanIn(BaseModel):
    path: Url
    ai: bool = False


class PackageIn(BaseModel):
    name: Short
    ecosystem: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    package_key: Annotated[str, StringConstraints(max_length=400)] = ""
    declared_spec: Short = ""
    resolved_version: Annotated[str, StringConstraints(max_length=120)] = ""
    manifest_path: Url = ""
    scope: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)] = "runtime"
    purl: Annotated[str, StringConstraints(max_length=600)] = ""


class ApplyScanIn(BaseModel):
    path: Url
    stack: Terms = Field(default_factory=list)
    packages: Annotated[list[PackageIn], Field(max_length=500)] = Field(default_factory=list)  # the recognised dependencies, by ecosystem
    needs: Terms = Field(default_factory=list)
    keywords: Terms | None = None   # None keeps the project's keywords
    description: Text | None = None      # None keeps the description


class DirectoryScanIn(BaseModel):
    path: Url = ""


class DirectoryProjectIn(ProjectIn):
    path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    stack: Terms = Field(default_factory=list)
    packages: Annotated[list[PackageIn], Field(max_length=500)] = Field(default_factory=list)


class DirectoryImportIn(BaseModel):
    path: Url
    projects: Annotated[list[DirectoryProjectIn], Field(min_length=1, max_length=100)]


class AddUrlIn(BaseModel):
    url: Url
    analyze: bool = True


class OpportunityStateIn(BaseModel):
    state: Short


class DecisionIn(BaseModel):
    status: Short
    goal: Text | None = None
    verdict: Text | None = None
    criteria: Text | None = None       # what would count as success
    revisit_when: Text | None = None   # what change would make you look again


class ChatIn(BaseModel):
    message: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    history: Annotated[list[dict], Field(max_length=40)] = Field(default_factory=list)


class CollectIn(BaseModel):
    sources: Annotated[list[Short], Field(max_length=20)] | None = None


class QueryIn(BaseModel):
    project_slug: Short
    source: Short
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class QueryPatch(BaseModel):
    enabled: bool


class RegenerateIn(BaseModel):
    project_slug: Short | None = None  # None = every project
    ai: bool = True


class FeedbackIn(BaseModel):
    """Either a vote (project_slug + verdict) or a note — exactly one of the two."""
    project_slug: Short | None = None
    verdict: int | None = None
    note: Text | None = None
