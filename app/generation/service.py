"""Grounded answer generation over hybrid-retrieved chunks, with optional multi-turn memory."""

import uuid

from app.core.db import get_session_factory
from app.generation.client import LLMClient, OllamaLLMClient
from app.generation.config import GenerationSettings, get_generation_settings
from app.generation.prompt import SYSTEM_PROMPT, build_prompt
from app.generation.repository import append_message, get_or_create_conversation, get_recent_messages
from app.generation.rewrite import rewrite_query
from app.generation.schemas import Citation, ConversationTurn, GenerationResponse
from app.retrieval.schemas import RetrievedChunk
from app.retrieval.service import search as retrieval_search

NO_CONTEXT_ANSWER = "I don't have enough information in the ingested documents to answer this question."


def _citations_for(chunks: list[RetrievedChunk]) -> list[Citation]:
    return [
        Citation(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            section_path=chunk.section_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            source_filename=chunk.source_filename,
        )
        for chunk in chunks
    ]


def generate(
    query: str,
    top_k: int,
    rerank: bool = False,
    expand_sections: bool = False,
    conversation_id: uuid.UUID | None = None,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
) -> GenerationResponse:
    """Retrieve context for `query` and synthesize a grounded, citation-marked answer.

    `conversation_id` is a stateless/stateful switch. `None` (the default) is fully
    stateless: no session opened, no history loaded, no rewriting, nothing persisted --
    behavior is identical to the single-turn-only version of this function. Given a
    `conversation_id`, the last `settings.history_window_turns` messages are loaded (empty
    on a conversation's first turn) via a short-lived read session that is closed before
    rewriting/retrieval/generation run; no DB session is held open across those LLM calls.
    If history exists, `query` is rewritten into a standalone retrieval query via
    `rewrite_query` before running retrieval, and that history is rendered into the
    generation prompt. Both the raw user turn and the assistant's answer are persisted
    together in one transaction via a second, separately opened write session, but only
    after generation succeeds -- a failure commits nothing (the write session isn't even
    opened until `answer`/`citations` are fully computed).

    Runs the existing hybrid retrieval pipeline unmodified (`rerank`/`expand_sections`
    passed straight through). If retrieval returns no chunks, short-circuits to
    `NO_CONTEXT_ANSWER` without constructing or calling an `LLMClient` for the final answer
    (a conversational short-circuit still persists both turns, so the conversation record
    reflects that the question went unanswered).

    `settings`/`llm_client` are injectable for testing; default to the process-wide
    cached `GenerationSettings` and an `OllamaLLMClient` built from it.
    """
    settings = settings or get_generation_settings()

    if conversation_id is None:
        chunks = retrieval_search(query, top_k, rerank=rerank, expand_sections=expand_sections)
        if not chunks:
            return GenerationResponse(answer=NO_CONTEXT_ANSWER, citations=[], conversation_id=None)

        llm_client = llm_client or OllamaLLMClient(settings)
        user_prompt, included_chunks = build_prompt(query, chunks, settings.max_context_chars)
        answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)
        return GenerationResponse(
            answer=answer, citations=_citations_for(included_chunks), conversation_id=None
        )

    session_factory = get_session_factory()

    with session_factory() as read_session:
        history_records = get_recent_messages(
            read_session, conversation_id, settings.history_window_turns
        )
        history = [ConversationTurn(role=r.role, content=r.content) for r in history_records]

    if history:
        llm_client = llm_client or OllamaLLMClient(settings)
        rewritten_query = rewrite_query(query, history, llm_client)
    else:
        rewritten_query = query

    chunks = retrieval_search(rewritten_query, top_k, rerank=rerank, expand_sections=expand_sections)
    if not chunks:
        answer = NO_CONTEXT_ANSWER
        citations: list[Citation] = []
    else:
        llm_client = llm_client or OllamaLLMClient(settings)
        user_prompt, included_chunks = build_prompt(
            query, chunks, settings.max_context_chars, history=history
        )
        answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)
        citations = _citations_for(included_chunks)

    with session_factory() as write_session:
        get_or_create_conversation(write_session, conversation_id)
        append_message(write_session, conversation_id, "user", query)
        append_message(write_session, conversation_id, "assistant", answer)
        write_session.commit()

    return GenerationResponse(answer=answer, citations=citations, conversation_id=conversation_id)
