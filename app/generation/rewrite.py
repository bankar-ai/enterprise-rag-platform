"""LLM-based query rewriting for retrieval, using conversation history."""

from app.generation.client import LLMClient
from app.generation.schemas import ConversationTurn

REWRITE_SYSTEM_PROMPT = (
    "Given a conversation history and a follow-up question, rephrase the follow-up "
    "question into a standalone question that can be understood without the conversation "
    "history. Do not answer the question -- only rephrase it. If the follow-up question "
    "is already standalone, return it unchanged. Reply with only the rephrased question, "
    "nothing else."
)


def rewrite_query(query: str, history: list[ConversationTurn], llm_client: LLMClient) -> str:
    """Rephrase `query` into a standalone retrieval query using `history`.

    Only meaningful when `history` is non-empty -- callers should skip invoking this
    entirely for a conversation's first turn (see `app/generation/service.py`).
    """
    history_lines = [f"{turn.role}: {turn.content}" for turn in history]
    history_block = "\n".join(history_lines)
    user_prompt = f"Conversation history:\n{history_block}\n\nFollow-up question: {query}"
    return llm_client.generate(REWRITE_SYSTEM_PROMPT, user_prompt).strip()
