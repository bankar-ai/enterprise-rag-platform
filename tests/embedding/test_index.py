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
