from app.generation.rewrite import rewrite_query
from app.generation.schemas import ConversationTurn


class _FakeLLMClient:
    def __init__(self, answer):
        self._answer = answer
        self.calls = []

    def generate(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        return self._answer


def test_rewrite_query_includes_history_and_question_in_prompt():
    history = [
        ConversationTurn(role="user", content="what is the deployment process?"),
        ConversationTurn(role="assistant", content="it has three steps: build, test, deploy."),
    ]
    fake_llm = _FakeLLMClient("  what is the second step in the deployment process?  ")

    result = rewrite_query("what about the second one?", history, fake_llm)

    assert result == "what is the second step in the deployment process?"
    assert len(fake_llm.calls) == 1
    system_prompt, user_prompt = fake_llm.calls[0]
    assert "rephrase" in system_prompt.lower()
    assert "standalone" in system_prompt.lower()
    assert "user: what is the deployment process?" in user_prompt.lower()
    assert "assistant: it has three steps" in user_prompt.lower()
    assert "what about the second one?" in user_prompt.lower()


def test_rewrite_query_strips_whitespace_from_llm_response():
    history = [ConversationTurn(role="user", content="hi")]
    fake_llm = _FakeLLMClient("\n  already standalone question?  \n")

    result = rewrite_query("already standalone question?", history, fake_llm)

    assert result == "already standalone question?"
