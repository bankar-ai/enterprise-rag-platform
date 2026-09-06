from app.evaluation.dataset import GOLDEN_DOCUMENTS, GOLDEN_QUERIES


def test_every_query_references_an_existing_document_label():
    labels = {doc.label for doc in GOLDEN_DOCUMENTS}
    for query in GOLDEN_QUERIES:
        assert query.document_label in labels


def test_every_expected_chunk_index_exists_in_its_document():
    chunks_by_label = {doc.label: {c.index for c in doc.chunks} for doc in GOLDEN_DOCUMENTS}
    for query in GOLDEN_QUERIES:
        available = chunks_by_label[query.document_label]
        for expected_index in query.expected_chunk_indices:
            assert expected_index in available, (
                f"query {query.query!r} expects chunk_index {expected_index} "
                f"in document {query.document_label!r}, but only {available} exist"
            )


def test_every_query_has_at_least_one_expected_chunk():
    for query in GOLDEN_QUERIES:
        assert len(query.expected_chunk_indices) > 0
