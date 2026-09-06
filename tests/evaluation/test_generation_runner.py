import os

from app.core.db import get_session_factory
from app.embedding.index import FaissIndex
from app.evaluation.dataset import GOLDEN_QUERIES
from app.evaluation.generation_runner import run_generation_evaluation
from app.evaluation.models import GenerationEvaluationRunRecord
from app.evaluation.schemas import GenerationScores


class _FixedFakeJudge:
    def score(self, user_input, response, retrieved_contexts):
        return GenerationScores(faithfulness=0.9, answer_relevancy=0.8, context_precision=0.7)


class _FakeLLMClient:
    def generate(self, system_prompt, user_prompt):
        return "a fake grounded answer"

    def generate_stream(self, system_prompt, user_prompt):
        raise NotImplementedError


class _DiscriminatingFakeEmbeddingClient:
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


def test_run_generation_evaluation_persists_and_cleans_up(tmp_path):
    faiss_index = FaissIndex(str(tmp_path / "gen-eval.index"), dimension=32)

    summary = run_generation_evaluation(
        judge=_FixedFakeJudge(),
        llm_client=_FakeLLMClient(),
        embedding_client=_DiscriminatingFakeEmbeddingClient(),
        faiss_index=faiss_index,
    )

    assert summary.judge == "_FixedFakeJudge"
    assert summary.num_queries == len(GOLDEN_QUERIES)
    assert summary.mean_faithfulness == 0.9
    assert all(r.answer == "a fake grounded answer" for r in summary.per_query)

    session_factory = get_session_factory()
    with session_factory() as session:
        latest = (
            session.query(GenerationEvaluationRunRecord)
            .order_by(GenerationEvaluationRunRecord.run_at.desc())
            .first()
        )
        assert latest is not None
        assert latest.num_queries == len(GOLDEN_QUERIES)


def test_run_generation_evaluation_builds_and_removes_its_own_temp_faiss_index_when_none_injected(
    monkeypatch,
):
    """No `faiss_index` injected: the runner builds and later deletes its own temp-backed index."""
    monkeypatch.setattr(
        "app.evaluation.generation_runner.get_embedding_settings",
        lambda: type("S", (), {"dimension": 32})(),
    )

    created_paths: list[str] = []
    from app.embedding.index import FaissIndex as RealFaissIndex

    class _TrackingFaissIndex(RealFaissIndex):
        def __init__(self, path, dimension):
            created_paths.append(path)
            super().__init__(path, dimension)

    monkeypatch.setattr("app.evaluation.generation_runner.FaissIndex", _TrackingFaissIndex)

    run_generation_evaluation(
        judge=_FixedFakeJudge(),
        llm_client=_FakeLLMClient(),
        embedding_client=_DiscriminatingFakeEmbeddingClient(),
    )

    assert len(created_paths) == 1
    assert not os.path.exists(created_paths[0])
