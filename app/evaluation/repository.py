"""Persistence for evaluation run summaries, and cleanup of an eval run's transient data."""

import uuid

from sqlalchemy.orm import Session

from app.auth.models import UserRecord
from app.evaluation.models import EvaluationRunRecord
from app.evaluation.schemas import EvaluationSummary
from app.ingestion.models import ChunkRecord, DocumentRecord


def save_evaluation_run(session: Session, summary: EvaluationSummary) -> EvaluationRunRecord:
    """Persist `summary` as a new `EvaluationRunRecord`. Does not commit."""
    record = EvaluationRunRecord(
        top_k=summary.top_k,
        num_queries=summary.num_queries,
        mean_precision_at_k=summary.mean_precision,
        mean_recall_at_k=summary.mean_recall,
        mrr=summary.mrr,
        details=[result.model_dump() for result in summary.per_query],
    )
    session.add(record)
    session.flush()
    return record


def cleanup_eval_data(session: Session, document_ids: list[str], owner_id: uuid.UUID) -> None:
    """Delete the chunks, documents, and user created by one eval run. Does not commit.

    Order matters: `chunks.document_id` and `documents.owner_id` are both plain (non-cascading)
    foreign keys, so children must be deleted before their parents.
    """
    for document_id in document_ids:
        session.query(ChunkRecord).filter(ChunkRecord.document_id == document_id).delete()
    for document_id in document_ids:
        session.query(DocumentRecord).filter(DocumentRecord.document_id == document_id).delete()
    session.query(UserRecord).filter(UserRecord.id == owner_id).delete()
