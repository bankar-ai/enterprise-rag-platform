from app.generation.config import GenerationSettings
from app.generation.service import NO_CONTEXT_ANSWER, generate
from app.retrieval.schemas import RetrievedChunk


def _chunk(chunk_id):
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        text=f"text for {chunk_id}",
        section_path=["Intro"],
        page_start=1,
        page_end=1,
        source_filename="doc.pdf",
        score=0.9,
    )


class _FakeLLMClient:
    def __init__(self, answer):
        self._answer = answer
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._answer


def test_generate_short_circuits_on_empty_retrieval(monkeypatch):
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [])
    fake_llm = _FakeLLMClient("should not be used")

    response = generate("what is X?", top_k=5, llm_client=fake_llm)

    assert response.answer == NO_CONTEXT_ANSWER
    assert response.citations == []
    assert fake_llm.calls == []


def test_generate_builds_prompt_and_returns_citations(monkeypatch):
    chunks = [_chunk("c1"), _chunk("c2")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1][2]")

    response = generate(
        "what is X?",
        top_k=5,
        rerank=True,
        expand_sections=False,
        settings=GenerationSettings(),
        llm_client=fake_llm,
    )

    assert response.answer == "the answer [1][2]"
    assert [c.chunk_id for c in response.citations] == ["c1", "c2"]
    assert len(fake_llm.calls) == 1
    system_prompt, user_prompt = fake_llm.calls[0]
    assert "[1]" in user_prompt and "[2]" in user_prompt
    assert "cite sources inline" in system_prompt.lower()


def test_generate_passes_retrieval_params_through(monkeypatch):
    captured = {}

    def _fake_search(query, top_k, rerank=False, expand_sections=False):
        captured["args"] = (query, top_k, rerank, expand_sections)
        return [_chunk("c1")]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    fake_llm = _FakeLLMClient("answer")

    generate("q", top_k=7, rerank=True, expand_sections=True, llm_client=fake_llm)

    assert captured["args"] == ("q", 7, True, True)
