"""A FAISS vector index persisted to a local file."""

import os
from typing import cast

import faiss
import numpy as np

from app.core.telemetry import get_tracer


class FaissIndex:
    """A flat L2 FAISS index, addressable by explicit int64 IDs, persisted to `path`."""

    def __init__(self, path: str, dimension: int) -> None:
        """Load the index at `path` if it exists, otherwise create an empty one."""
        self._path = path
        self._dimension = dimension
        self._index = self._load_or_create()

    def _load_or_create(self) -> faiss.IndexIDMap:
        if os.path.exists(self._path):
            return cast(faiss.IndexIDMap, faiss.read_index(self._path))
        return faiss.IndexIDMap(faiss.IndexFlatL2(self._dimension))

    @property
    def ntotal(self) -> int:
        """Number of vectors currently in the index."""
        return int(self._index.ntotal)

    def add(self, vector_ids: list[int], vectors: list[list[float]]) -> None:
        """Add `vectors`, keyed by the parallel `vector_ids`. No-op if either is empty."""
        if not vector_ids:
            return
        ids = np.array(vector_ids, dtype="int64")
        matrix = np.array(vectors, dtype="float32")
        self._index.add_with_ids(matrix, ids)

    def save(self) -> None:
        """Persist the index to `path`, creating parent directories if needed."""
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        faiss.write_index(self._index, self._path)

    def search(self, vector: list[float], k: int) -> list[tuple[int, float]]:
        """Return up to `k` nearest `(vector_id, distance)` pairs, nearest-first.

        Empty list if the index has no vectors or `k <= 0`. Padding entries FAISS
        returns when the index has fewer than `k` vectors (`vector_id == -1`) are
        dropped.
        """
        with get_tracer().start_as_current_span("faiss.search") as span:
            span.set_attribute("faiss.k", k)
            if self._index.ntotal == 0 or k <= 0:
                span.set_attribute("faiss.hits", 0)
                return []
            query = np.array([vector], dtype="float32")
            distances, ids = self._index.search(query, k)
            hits = [
                (int(vector_id), float(distance))
                for vector_id, distance in zip(ids[0], distances[0], strict=True)
                if vector_id != -1
            ]
            span.set_attribute("faiss.hits", len(hits))
            return hits
