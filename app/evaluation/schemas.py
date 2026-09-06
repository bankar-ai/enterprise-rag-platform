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
