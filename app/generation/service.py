"""Grounded answer generation over hybrid-retrieved chunks."""

from app.generation.client import LLMClient, OllamaLLMClient
from app.generation.config import GenerationSettings, get_generation_settings
from app.generation.prompt import SYSTEM_PROMPT, build_prompt
from app.generation.schemas import Citation, GenerationResponse
from app.retrieval.service import search as retrieval_search

NO_CONTEXT_ANSWER = "I don't have enough information in the ingested documents to answer this question."


def generate(
    query: str,
    top_k: int,
    rerank: bool = False,
    expand_sections: bool = False,
    settings: GenerationSettings | None = None,
    llm_client: LLMClient | None = None,
) -> GenerationResponse:
    """Retrieve context for `query` and synthesize a grounded, citation-marked answer.

    Runs the existing hybrid retrieval pipeline unmodified (`rerank`/`expand_sections`
    passed straight through). If retrieval returns no chunks, short-circuits to
    `NO_CONTEXT_ANSWER` without constructing or calling an `LLMClient`. Otherwise builds
    a numbered, char-budget-truncated prompt from the retrieved chunks and returns the
    LLM's answer alongside `Citation`s for every chunk actually included in the prompt.

    `settings`/`llm_client` are injectable for testing; default to the process-wide
    cached `GenerationSettings` and an `OllamaLLMClient` built from it.
    """
    chunks = retrieval_search(query, top_k, rerank=rerank, expand_sections=expand_sections)
    if not chunks:
        return GenerationResponse(answer=NO_CONTEXT_ANSWER, citations=[])

    settings = settings or get_generation_settings()
    llm_client = llm_client or OllamaLLMClient(settings)

    user_prompt, included_chunks = build_prompt(query, chunks, settings.max_context_chars)
    answer = llm_client.generate(SYSTEM_PROMPT, user_prompt)

    citations = [
        Citation(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            section_path=chunk.section_path,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            source_filename=chunk.source_filename,
        )
        for chunk in included_chunks
    ]
    return GenerationResponse(answer=answer, citations=citations)
