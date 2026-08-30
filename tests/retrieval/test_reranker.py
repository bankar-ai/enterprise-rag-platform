from app.retrieval.config import RerankerSettings
from app.retrieval.reranker import FlashRankReranker
from app.retrieval.schemas import RetrievedChunk


def _chunk(chunk_id: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        text=text,
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.5,
    )


def test_flashrank_reranker_ranks_on_topic_passage_first(tmp_path):
    reranker = FlashRankReranker(RerankerSettings(cache_dir=str(tmp_path)))
    candidates = [
        _chunk("off-topic", "bananas are a yellow tropical fruit"),
        _chunk("on-topic", "Paris is the capital city of France"),
    ]

    results = reranker.rerank("what is the capital of France?", candidates)

    assert [result.chunk_id for result in results] == ["on-topic", "off-topic"]
    assert results[0].score > results[1].score


def test_flashrank_reranker_empty_candidates_returns_empty_list_without_loading_model(tmp_path, monkeypatch):
    reranker = FlashRankReranker(RerankerSettings(cache_dir=str(tmp_path)))

    def _fail_if_called(self, request):
        raise AssertionError("Ranker.rerank should not be called for an empty candidate list")

    monkeypatch.setattr(type(reranker._ranker), "rerank", _fail_if_called)

    assert reranker.rerank("anything", []) == []
