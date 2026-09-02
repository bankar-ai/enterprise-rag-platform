"""Prompt construction for LLM-backed answer generation."""

from app.generation.schemas import ConversationTurn
from app.retrieval.schemas import RetrievedChunk

SYSTEM_PROMPT = (
    "You are an assistant answering questions using only the provided context. "
    "Cite sources inline using [1], [2], etc. matching the numbered context below. "
    "If the context does not contain enough information to answer, say so explicitly "
    "-- do not use outside knowledge. Any bracketed markers appearing in the 'Previous "
    "conversation' section belong to a different, earlier numbered context and do not "
    "correspond to the numbered context below -- ignore them and only use citation "
    "markers you assign yourself based on the numbered context below."
)


def build_prompt(
    query: str,
    chunks: list[RetrievedChunk],
    max_context_chars: int,
    history: list[ConversationTurn] | None = None,
) -> tuple[str, list[RetrievedChunk]]:
    """Build the numbered-context user prompt, truncated to `max_context_chars`.

    Walks `chunks` in order, accumulating character count. The first chunk is always
    included even if it alone exceeds the budget (so a single oversized top result
    doesn't produce empty context); every subsequent chunk is included only if adding
    it would not exceed `max_context_chars`. Returns the user-prompt text and the list
    of chunks actually included, in citation-number order.

    `history`, if given, is rendered as a chronological transcript under a "Previous
    conversation:" header, before the numbered context block -- omitted or `[]` produces
    output identical to no `history` argument at all.
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

    history_lines = [f"{turn.role}: {turn.content}" for turn in history or []]
    history_block = "Previous conversation:\n" + "\n".join(history_lines) if history_lines else ""

    parts = [part for part in (history_block, context_block, f"Question: {query}") if part]
    user_prompt = "\n\n".join(parts)
    return user_prompt, included
