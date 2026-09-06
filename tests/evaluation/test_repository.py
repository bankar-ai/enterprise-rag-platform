import uuid

from app.auth.repository import create_user
from app.core.db import get_session_factory
from app.evaluation.models import EvaluationRunRecord, GenerationEvaluationRunRecord
from app.evaluation.repository import (
    cleanup_eval_data,
    save_evaluation_run,
    save_generation_evaluation_run,
)
from app.evaluation.schemas import (
    EvaluationSummary,
    GenerationEvaluationSummary,
    GenerationQueryResult,
    QueryResult,
)
from app.ingestion.repository import get_chunks_by_vector_ids, save_document_and_chunks
from app.ingestion.schemas import Chunk


def _summary() -> EvaluationSummary:
    return EvaluationSummary(
        top_k=3,
        num_queries=1,
        mean_precision=0.5,
        mean_recall=1.0,
        mrr=1.0,
        per_query=[
            QueryResult(
                query="q",
                precision=0.5,
                recall=1.0,
                reciprocal_rank=1.0,
                retrieved_chunk_ids=["a-0"],
                relevant_chunk_ids=["a-0"],
            )
        ],
    )


def test_save_evaluation_run_persists_summary_fields():
    session_factory = get_session_factory()
    with session_factory() as session:
        record = save_evaluation_run(session, _summary())
        session.commit()

        assert record.top_k == 3
        assert record.num_queries == 1
        assert record.mean_precision_at_k == 0.5
        assert record.mean_recall_at_k == 1.0
        assert record.mrr == 1.0
        assert record.details[0]["query"] == "q"

        record_id = record.id
        session.query(EvaluationRunRecord).filter(EvaluationRunRecord.id == record_id).delete()
        session.commit()


def test_cleanup_eval_data_removes_chunks_documents_and_user():
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, f"eval-cleanup-{uuid.uuid4()}@test", "x")
        session.flush()
        document_id = str(uuid.uuid4())
        chunk = Chunk(
            chunk_id=f"{document_id}-0",
            document_id=document_id,
            chunk_index=0,
            text="text",
            section_path=["A"],
            page_start=1,
            page_end=1,
            char_count=4,
            parser_used="fast",
            source_filename="doc.pdf",
        )
        records = save_document_and_chunks(session, document_id, "doc.pdf", [chunk], user.id)
        vector_id = records[0].vector_id
        session.commit()

        cleanup_eval_data(session, [document_id], user.id)
        session.commit()

        assert get_chunks_by_vector_ids(session, [vector_id], user.id) == {}


def _generation_summary() -> GenerationEvaluationSummary:
    return GenerationEvaluationSummary(
        judge="ragas",
        num_queries=1,
        mean_faithfulness=0.9,
        mean_answer_relevancy=0.8,
        mean_context_precision=0.7,
        per_query=[
            GenerationQueryResult(
                query="q",
                answer="a",
                faithfulness=0.9,
                answer_relevancy=0.8,
                context_precision=0.7,
            )
        ],
    )


def test_save_generation_evaluation_run_persists_summary_fields():
    session_factory = get_session_factory()
    with session_factory() as session:
        record = save_generation_evaluation_run(session, _generation_summary())
        session.commit()

        assert record.judge == "ragas"
        assert record.num_queries == 1
        assert record.mean_faithfulness == 0.9
        assert record.mean_answer_relevancy == 0.8
        assert record.mean_context_precision == 0.7
        assert record.details[0]["query"] == "q"

        record_id = record.id
        session.query(GenerationEvaluationRunRecord).filter(
            GenerationEvaluationRunRecord.id == record_id
        ).delete()
        session.commit()
