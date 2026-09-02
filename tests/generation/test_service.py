import uuid

from app.core.db import get_session_factory
from app.generation.config import GenerationSettings
from app.generation.repository import append_message, get_or_create_conversation, get_recent_messages
from app.generation.service import NO_CONTEXT_ANSWER, generate, get_conversation_history
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


def test_generate_with_new_conversation_id_creates_conversation_and_persists_turns(monkeypatch):
    conversation_id = uuid.uuid4()
    chunks = [_chunk("c1")]
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: chunks)
    fake_llm = _FakeLLMClient("the answer [1]")

    response = generate(
        "what is X?", top_k=5, conversation_id=conversation_id, llm_client=fake_llm
    )

    assert response.conversation_id == conversation_id
    assert response.answer == "the answer [1]"

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "what is X?"
    assert messages[1].content == "the answer [1]"


def test_generate_first_turn_of_conversation_does_not_call_rewrite(monkeypatch):
    conversation_id = uuid.uuid4()
    monkeypatch.setattr(
        "app.generation.service.retrieval_search", lambda *a, **k: [_chunk("c1")]
    )
    rewrite_calls = []
    monkeypatch.setattr(
        "app.generation.service.rewrite_query",
        lambda *a, **k: rewrite_calls.append(a) or "should not be reached",
    )
    fake_llm = _FakeLLMClient("answer")

    generate("what is X?", top_k=5, conversation_id=conversation_id, llm_client=fake_llm)

    assert rewrite_calls == []


def test_generate_second_turn_rewrites_query_using_history(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_or_create_conversation(session, conversation_id)
        from app.generation.repository import append_message

        append_message(session, conversation_id, "user", "what is the deployment process?")
        append_message(session, conversation_id, "assistant", "it has three steps.")
        session.commit()

    captured_retrieval_query = {}

    def _fake_search(query, top_k, rerank=False, expand_sections=False):
        captured_retrieval_query["query"] = query
        return [_chunk("c1")]

    monkeypatch.setattr("app.generation.service.retrieval_search", _fake_search)
    monkeypatch.setattr(
        "app.generation.service.rewrite_query",
        lambda query, history, llm_client: "what is the second step in the deployment process?",
    )
    fake_llm = _FakeLLMClient("the second step is test.")

    response = generate(
        "what about the second one?",
        top_k=5,
        conversation_id=conversation_id,
        llm_client=fake_llm,
    )

    assert captured_retrieval_query["query"] == "what is the second step in the deployment process?"
    assert response.conversation_id == conversation_id

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.content for m in messages] == [
        "what is the deployment process?",
        "it has three steps.",
        "what about the second one?",
        "the second step is test.",
    ]


def test_generate_conversation_rewrite_failure_propagates_and_commits_nothing(monkeypatch):
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_or_create_conversation(session, conversation_id)
        from app.generation.repository import append_message

        append_message(session, conversation_id, "user", "first question")
        append_message(session, conversation_id, "assistant", "first answer")
        session.commit()

    def _raise_rewrite(query, history, llm_client):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr("app.generation.service.rewrite_query", _raise_rewrite)
    monkeypatch.setattr(
        "app.generation.service.retrieval_search",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retrieval should not run")),
    )
    fake_llm = _FakeLLMClient("should not be reached")

    try:
        generate(
            "a follow-up", top_k=5, conversation_id=conversation_id, llm_client=fake_llm
        )
        raise AssertionError("expected RuntimeError to propagate")
    except RuntimeError as exc:
        assert str(exc) == "ollama unreachable"

    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.content for m in messages] == ["first question", "first answer"]


def test_generate_conversation_short_circuit_still_persists_turns(monkeypatch):
    conversation_id = uuid.uuid4()
    monkeypatch.setattr("app.generation.service.retrieval_search", lambda *a, **k: [])
    fake_llm = _FakeLLMClient("should not be used")

    response = generate(
        "unanswerable question", top_k=5, conversation_id=conversation_id, llm_client=fake_llm
    )

    assert response.answer == NO_CONTEXT_ANSWER
    assert fake_llm.calls == []

    session_factory = get_session_factory()
    with session_factory() as session:
        messages = get_recent_messages(session, conversation_id, limit=10)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].content == NO_CONTEXT_ANSWER


def test_get_conversation_history_returns_none_for_unknown_id():
    assert get_conversation_history(uuid.uuid4()) is None


def test_get_conversation_history_returns_all_messages_oldest_first():
    conversation_id = uuid.uuid4()
    session_factory = get_session_factory()
    with session_factory() as session:
        get_or_create_conversation(session, conversation_id)
        append_message(session, conversation_id, "user", "first")
        append_message(session, conversation_id, "assistant", "second")
        session.commit()

    history = get_conversation_history(conversation_id)

    assert history is not None
    assert history.conversation_id == conversation_id
    assert [m.content for m in history.messages] == ["first", "second"]
    assert [m.role for m in history.messages] == ["user", "assistant"]
