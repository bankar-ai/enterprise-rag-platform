"""Pydantic schemas for one evaluation run's per-query results and aggregate summary."""

from pydantic import BaseModel


class QueryResult(BaseModel):
    """One golden query's scored result."""

    query: str
    precision: float
    recall: float
    reciprocal_rank: float
    retrieved_chunk_ids: list[str]
    relevant_chunk_ids: list[str]


class EvaluationSummary(BaseModel):
    """Aggregate result of one full evaluation run."""

    top_k: int
    num_queries: int
    mean_precision: float
    mean_recall: float
    mrr: float
    per_query: list[QueryResult]


class GenerationScores(BaseModel):
    """One query's generation-quality scores, each in [0.0, 1.0]."""

    faithfulness: float
    answer_relevancy: float
    context_precision: float


class GenerationQueryResult(BaseModel):
    """One golden query's generated answer and its scored quality."""

    query: str
    answer: str
    faithfulness: float
    answer_relevancy: float
    context_precision: float


class GenerationEvaluationSummary(BaseModel):
    """Aggregate result of one full generation-quality evaluation run."""

    judge: str
    num_queries: int
    mean_faithfulness: float
    mean_answer_relevancy: float
    mean_context_precision: float
    per_query: list[GenerationQueryResult]
