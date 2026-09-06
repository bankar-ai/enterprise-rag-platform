from app.evaluation.metrics import precision_at_k, recall_at_k, reciprocal_rank


def test_precision_at_k_counts_relevant_among_top_k():
    retrieved = ["a", "b", "c"]
    relevant = {"a", "c"}
    assert precision_at_k(retrieved, relevant, k=3) == 2 / 3


def test_precision_at_k_uses_k_not_len_retrieved_as_denominator():
    retrieved = ["a"]
    relevant = {"a"}
    assert precision_at_k(retrieved, relevant, k=3) == 1 / 3


def test_precision_at_k_only_considers_top_k_items():
    retrieved = ["z", "z", "a"]  # "a" (relevant) is ranked 3rd
    relevant = {"a"}
    assert precision_at_k(retrieved, relevant, k=2) == 0.0


def test_recall_at_k_divides_by_total_relevant_count():
    retrieved = ["a", "z", "z"]
    relevant = {"a", "b"}
    assert recall_at_k(retrieved, relevant, k=3) == 1 / 2


def test_recall_at_k_with_no_relevant_items_is_zero_not_a_division_error():
    assert recall_at_k(["a"], set(), k=3) == 0.0


def test_reciprocal_rank_of_first_relevant_item():
    retrieved = ["z", "a", "b"]
    relevant = {"a"}
    assert reciprocal_rank(retrieved, relevant) == 1 / 2


def test_reciprocal_rank_is_zero_when_nothing_relevant_is_retrieved():
    assert reciprocal_rank(["z", "y"], {"a"}) == 0.0


def test_reciprocal_rank_of_empty_retrieved_list_is_zero():
    assert reciprocal_rank([], {"a"}) == 0.0
