import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.graphrag.params import Pipeline, RetrievalParams, Technique

MAX_QUERY_CHARS = 1000


class GraphRagParams(BaseModel):
    """Per-request overrides of the technique parameters."""

    technique: Technique = Technique.SPREADING
    max_hops: int = Field(default=2, ge=1, le=4)
    decay: float = Field(default=0.7, gt=0, le=1)
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    hub_damping: bool = True
    seed_limit: int = Field(default=6, ge=1, le=15)
    ppr_alpha: float = Field(default=0.5, gt=0, lt=1)
    path_max_len: int = Field(default=3, ge=1, le=5)
    relation_weights: dict[str, float] = Field(default_factory=dict)

    def to_params(self) -> RetrievalParams:
        return RetrievalParams(**self.model_dump())


class _QueryBase(BaseModel):
    subject_id: uuid.UUID
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    params: GraphRagParams = Field(default_factory=GraphRagParams)

    @field_validator("query")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be blank")
        return v


class GraphRagQueryRequest(_QueryBase):
    # "graph" = graph evidence only; "hybrid" = fused with the vector pipeline.
    pipeline: Pipeline = Pipeline.GRAPH

    @field_validator("pipeline")
    @classmethod
    def _not_vector(cls, v: Pipeline) -> Pipeline:
        if v == Pipeline.VECTOR:
            raise ValueError("use POST /query for the vector pipeline")
        return v


class CompareRequest(_QueryBase):
    pipelines: list[Pipeline] = Field(
        default_factory=lambda: [Pipeline.VECTOR, Pipeline.GRAPH], min_length=1, max_length=3
    )


class ExpandRequest(BaseModel):
    subject_id: uuid.UUID
    entity_ids: list[str] = Field(min_length=1, max_length=20)
    limit: int = Field(default=60, ge=1, le=300)


class IndexRequest(BaseModel):
    communities: bool = True


class RagRunRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    created_at: datetime
    compare_id: uuid.UUID | None
    pipeline: str
    technique: str | None
    query: str
    status: str
    total_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    timings_ms: dict


class QuestionIn(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    expected_answer: str | None = None
    supporting_chunk_ids: list[str] = Field(default_factory=list)
    query_type: str = "factoid"


class BenchmarkRequest(BaseModel):
    subject_id: uuid.UUID
    label: str = Field(default="benchmark", max_length=120)
    questions: list[QuestionIn] = Field(min_length=1, max_length=200)
    techniques: list[Technique] = Field(default_factory=lambda: [Technique.SPREADING])
    include_hybrid: bool = True
    judge: bool = True
    params: GraphRagParams = Field(default_factory=GraphRagParams)


class BenchmarkRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    created_at: datetime
    label: str
    status: str
    error: str | None
    question_count: int
    config: dict
    aggregates: dict
    by_type: dict
    build_cost: dict


class BenchmarkDetail(BenchmarkRead):
    results: list
