from app.generation.client import OllamaLLMClient
from app.generation.config import GenerationSettings


class _FakeOllamaClient:
    def __init__(self, host):
        self.host = host
        self.calls = []

    def chat(self, model, messages, options):
        self.calls.append((model, messages, options))
        return {"message": {"content": "the answer [1]"}}


def test_generate_calls_ollama_with_system_and_user_messages(monkeypatch):
    fake = _FakeOllamaClient(host="http://fake:11434")
    monkeypatch.setattr("app.generation.client.ollama.Client", lambda host: fake)
    settings = GenerationSettings(
        ollama_host="http://fake:11434", model="test-model", temperature=0.2
    )

    client = OllamaLLMClient(settings)
    answer = client.generate("system text", "user text")

    assert answer == "the answer [1]"
    assert fake.calls == [
        (
            "test-model",
            [
                {"role": "system", "content": "system text"},
                {"role": "user", "content": "user text"},
            ],
            {"temperature": 0.2},
        )
    ]
