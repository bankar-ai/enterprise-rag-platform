"""Prompt construction for LLM-backed answer generation."""

from app.retrieval.schemas import RetrievedChunk

SYSTEM_PROMPT = (
    "You are an assistant answering questions using only the provided context. "
    "Cite sources inline using [1], [2], etc. matching the numbered context below. "
    "If the context does not contain enough information to answer, say so explicitly "
    "-- do not use outside knowledge."
)


def build_prompt(
    query: str, chunks: list[RetrievedChunk], max_context_chars: int
) -> tuple[str, list[RetrievedChunk]]:
    """Build the numbered-context user prompt, truncated to `max_context_chars`.

    Walks `chunks` in order, accumulating character count. The first chunk is always
    included even if it alone exceeds the budget (so a single oversized top result
    doesn't produce empty context); every subsequent chunk is included only if adding
    it would not exceed `max_context_chars`. Returns the user-prompt text and the list
    of chunks actually included, in citation-number order.
    """
    included: list[RetrievedChunk] = []
    total_chars = 0
    for chunk in chunks:
        entry_len = len(chunk.text)
        if included and total_chars + entry_len > max_context_chars:
            break
        included.append(chunk)
        total_chars += entry_len

    context_lines = []
    for index, chunk in enumerate(included, start=1):
        section = " > ".join(chunk.section_path) if chunk.section_path else "N/A"
        context_lines.append(
            f"[{index}] {chunk.text}\n(source: {chunk.source_filename}, section: {section})"
        )
    context_block = "\n\n".join(context_lines)

    user_prompt = f"{context_block}\n\nQuestion: {query}" if context_block else f"Question: {query}"
    return user_prompt, included
