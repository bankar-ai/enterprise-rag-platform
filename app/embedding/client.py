"""Embedding generation via a local Ollama server."""

from typing import Protocol

import ollama

from app.embedding.config import EmbeddingSettings


class EmbeddingClient(Protocol):
    """Anything that can turn a batch of texts into embedding vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order."""
        ...


class OllamaEmbeddingClient:
    """`EmbeddingClient` backed by a local Ollama server running `settings.model`."""

    def __init__(self, settings: EmbeddingSettings) -> None:
        """Build a client bound to `settings.ollama_host` and `settings.model`."""
        self._client = ollama.Client(host=settings.ollama_host)
        self._model = settings.model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per text in `texts`, in the same order."""
        if not texts:
            return []
        response = self._client.embed(model=self._model, input=texts)
        return list(response["embeddings"])
