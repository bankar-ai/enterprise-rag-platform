"""Pure retrieval-quality metrics: Precision@k, Recall@k, and reciprocal rank."""


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-`k` retrieved IDs that are in `relevant`.

    Divides by `k` (not `len(retrieved)`), so returning fewer than `k` results is penalized --
    matching the standard Precision@k definition.
    """
    top_k = retrieved[:k]
    hits = sum(1 for item in top_k if item in relevant)
    return hits / k


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fraction of all `relevant` IDs found within the top-`k` retrieved IDs.

    `0.0` if `relevant` is empty (no relevant items exist to find), rather than raising.
    """
    if not relevant:
        return 0.0
    top_k = retrieved[:k]
    hits = sum(1 for item in top_k if item in relevant)
    return hits / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """`1 / rank` of the first relevant ID in `retrieved` (1-indexed), or `0.0` if none is found."""
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0
