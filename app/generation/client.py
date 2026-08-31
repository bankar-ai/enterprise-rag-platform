"""Answer generation via a local Ollama chat model."""

from typing import Protocol

import ollama

from app.generation.config import GenerationSettings


class LLMClient(Protocol):
    """Anything that can turn a system+user prompt pair into an answer string."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's answer text for the given system/user prompts."""
        ...


class OllamaLLMClient:
    """`LLMClient` backed by a local Ollama chat model."""

    def __init__(self, settings: GenerationSettings) -> None:
        """Build a client bound to `settings.ollama_host`/`settings.model`/`settings.temperature`."""
        self._client = ollama.Client(host=settings.ollama_host)
        self._model = settings.model
        self._temperature = settings.temperature

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Send `system_prompt`/`user_prompt` to Ollama and return the response text."""
        response = self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": self._temperature},
        )
        return response["message"]["content"]
