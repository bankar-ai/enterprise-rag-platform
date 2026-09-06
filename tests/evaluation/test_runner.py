from app.core.db import get_session_factory
from app.embedding.index import FaissIndex
from app.evaluation.dataset import GOLDEN_DOCUMENTS, GOLDEN_QUERIES
from app.evaluation.models import EvaluationRunRecord
from app.evaluation.runner import run_evaluation
from app.ingestion.models import DocumentRecord


class _DiscriminatingFakeEmbeddingClient:
    """Returns a distinct one-hot-ish vector per distinct text, so FAISS can tell them apart.

    Real embeddings aren't needed to test the runner's own orchestration (persistence,
    cleanup, metric aggregation) -- only that distinguishable inputs produce distinguishable,
    correctly-ranked outputs. Every unique text seen gets its own fixed dimension "hot".
    """

    def __init__(self):
        self._dimension = 32
        self._assigned: dict[str, int] = {}

    def embed(self, texts):
        vectors = []
        for text in texts:
            if text not in self._assigned:
                self._assigned[text] = len(self._assigned) % self._dimension
            vector = [0.0] * self._dimension
            vector[self._assigned[text]] = 1.0
            vectors.append(vector)
        return vectors


def test_run_evaluation_persists_a_summary_row_and_cleans_up_transient_data(tmp_path):
    faiss_index = FaissIndex(str(tmp_path / "eval.index"), dimension=32)
    embedding_client = _DiscriminatingFakeEmbeddingClient()

    summary = run_evaluation(top_k=3, embedding_client=embedding_client, faiss_index=faiss_index)

    assert summary.num_queries == len(GOLDEN_QUERIES)
    assert summary.mean_precision > 0.0
    assert summary.mrr > 0.0

    session_factory = get_session_factory()
    with session_factory() as session:
        # Real evaluation_runs rows persist across runs by design (that's the durable history
        # this exists to build), so this asserts against the most recent row, not a total count.
        latest = session.query(EvaluationRunRecord).order_by(EvaluationRunRecord.run_at.desc()).first()
        assert latest is not None
        assert latest.num_queries == len(GOLDEN_QUERIES)

        remaining_docs = session.query(DocumentRecord).all()
        assert all(doc.filename not in {d.label for d in GOLDEN_DOCUMENTS} for doc in remaining_docs)
