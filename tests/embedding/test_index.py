from app.embedding.index import FaissIndex


def test_new_index_starts_empty(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    assert index.ntotal == 0


def test_add_increases_ntotal(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([1, 2], [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])
    assert index.ntotal == 2


def test_add_empty_is_a_noop(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([], [])
    assert index.ntotal == 0


def test_save_and_reload_preserves_vectors(tmp_path):
    path = str(tmp_path / "nested" / "index.bin")
    index = FaissIndex(path, dimension=4)
    index.add([1, 2], [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])
    index.save()

    reloaded = FaissIndex(path, dimension=4)
    assert reloaded.ntotal == 2


def test_search_on_empty_index_returns_empty_list(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    assert index.search([0.1, 0.2, 0.3, 0.4], k=5) == []


def test_search_returns_nearest_first(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add(
        [1, 2, 3],
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.9, 0.1, 0.0, 0.0],
        ],
    )
    results = index.search([1.0, 0.0, 0.0, 0.0], k=2)
    assert [vector_id for vector_id, _ in results] == [1, 3]
    assert results[0][1] < results[1][1]


def test_search_k_larger_than_ntotal_returns_all_available(tmp_path):
    index = FaissIndex(str(tmp_path / "index.bin"), dimension=4)
    index.add([1, 2], [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
    results = index.search([1.0, 0.0, 0.0, 0.0], k=10)
    assert len(results) == 2
    assert {vector_id for vector_id, _ in results} == {1, 2}
