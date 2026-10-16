from pydantic import BaseModel, ConfigDict, Field


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class RelevanceResult(StrictOutput):
    score: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=600)


class EnrichmentResult(StrictOutput):
    summary: str = Field(min_length=1, max_length=600)
    why_interesting: str = Field(max_length=400)
    capabilities: list[str] = Field(max_length=4)
    relevance: dict[str, RelevanceResult]


class OpportunityResult(StrictOutput):
    project_slug: str
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=2000)
    item_ids: list[int] = Field(max_length=6)


class OpportunitiesResult(StrictOutput):
    opportunities: list[OpportunityResult] = Field(max_length=33)


class BatchItemResult(EnrichmentResult):
    item_id: int


class BatchEnrichmentResult(StrictOutput):
    results: list[BatchItemResult] = Field(max_length=20)


class RelevanceOnlyItem(StrictOutput):
    item_id: int
    relevance: dict[str, RelevanceResult]


class RelevanceBatchResult(StrictOutput):
    results: list[RelevanceOnlyItem] = Field(max_length=40)


class ChatResult(StrictOutput):
    answer: str = Field(min_length=1, max_length=6000)
    item_ids: list[int] = Field(max_length=12)


class CapabilityGroup(StrictOutput):
    canonical: str = Field(min_length=1, max_length=80)
    members: list[str] = Field(min_length=2, max_length=40)


class CapabilityConsolidation(StrictOutput):
    groups: list[CapabilityGroup] = Field(max_length=60)


class ProjectQueries(StrictOutput):
    project_slug: str
    github: list[str] = Field(max_length=8)
    huggingface: list[str] = Field(max_length=6)
    arxiv: list[str] = Field(default_factory=list, max_length=5)


class ProfileProposal(StrictOutput):
    description: str = Field(max_length=500)
    keywords: list[str] = Field(max_length=12)
    stack: list[str] = Field(max_length=40)
    needs: list[str] = Field(max_length=10)


class ProjectGoalInference(StrictOutput):
    key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    priority: int = Field(ge=1, le=3)
    outcome: str = Field(max_length=800)


class ProjectSubsystemInference(StrictOutput):
    key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(max_length=800)
    status: str = Field(max_length=30)
    current_capabilities: list[str] = Field(default_factory=list, max_length=16)
    stack_evidence: list[str] = Field(default_factory=list, max_length=16)


class ProjectGapInference(StrictOutput):
    key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=400)
    subsystem_key: str = Field(max_length=80)
    goal_keys: list[str] = Field(default_factory=list, max_length=6)
    priority: int = Field(ge=1, le=3)
    why: str = Field(max_length=1000)
    current_state: str = Field(max_length=800)
    desired_state: str = Field(max_length=800)


class ProjectRiskInference(StrictOutput):
    title: str = Field(min_length=1, max_length=400)
    why: str = Field(max_length=1000)


class ProjectTechnicalModelInference(StrictOutput):
    summary: str = Field(max_length=1600)
    goals: list[ProjectGoalInference] = Field(default_factory=list, max_length=8)
    subsystems: list[ProjectSubsystemInference] = Field(default_factory=list, max_length=12)
    gaps: list[ProjectGapInference] = Field(default_factory=list, max_length=12)
    risks: list[ProjectRiskInference] = Field(default_factory=list, max_length=8)


class InferredProjectNeed(StrictOutput):
    title: str = Field(min_length=1, max_length=600)
    priority: int = Field(ge=1, le=3)
    rationale: str = Field(max_length=600)
    constraints: str = Field(max_length=2000)
    acceptance_criteria: str = Field(max_length=2000)
    search_terms: list[str] = Field(max_length=12)
    # Exact titles of other needs that must be solved first. Optional for backward-compatible
    # local/fake providers; project_intelligence validates and de-cycles the graph before saving it.
    depends_on: list[str] = Field(default_factory=list, max_length=4)
    # Stable gap keys from technical_model.gaps that this need resolves. A need may span a couple of
    # related gaps, but should normally point at one.
    gap_keys: list[str] = Field(default_factory=list, max_length=4)


class ProjectNeedInference(StrictOutput):
    summary: str = Field(max_length=1000)
    technical_model: ProjectTechnicalModelInference = Field(default_factory=lambda: ProjectTechnicalModelInference(summary=""))
    needs: list[InferredProjectNeed] = Field(max_length=8)


class QueryGeneration(StrictOutput):
    projects: list[ProjectQueries] = Field(max_length=40)
