import os

from app.core.db import get_session_factory
from app.embedding.index import OwnerFaissIndexStore
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
    faiss_index_store = OwnerFaissIndexStore(str(tmp_path), dimension=32)
    embedding_client = _DiscriminatingFakeEmbeddingClient()

    summary = run_evaluation(
        top_k=3, embedding_client=embedding_client, faiss_index_store=faiss_index_store
    )

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


def test_run_evaluation_builds_and_removes_its_own_temp_faiss_index_when_none_injected(monkeypatch):
    """The runner builds its own temp-dir-backed store and deletes it afterward.

    When `faiss_index_store` isn't injected, it must never use the real app's persisted
    one, and it must not leave leftover index directories accumulating in the OS temp dir
    across runs.
    """
    embedding_client = _DiscriminatingFakeEmbeddingClient()
    monkeypatch.setattr(
        "app.evaluation.runner.get_embedding_settings",
        lambda: type("S", (), {"dimension": 32})(),
    )

    created_dirs: list[str] = []
    from app.embedding.index import OwnerFaissIndexStore as RealOwnerFaissIndexStore

    class _TrackingOwnerFaissIndexStore(RealOwnerFaissIndexStore):
        def __init__(self, index_dir, dimension):
            created_dirs.append(index_dir)
            super().__init__(index_dir, dimension)

    monkeypatch.setattr("app.evaluation.runner.OwnerFaissIndexStore", _TrackingOwnerFaissIndexStore)

    run_evaluation(top_k=3, embedding_client=embedding_client)

    assert len(created_dirs) == 1
    assert not os.path.exists(created_dirs[0])
