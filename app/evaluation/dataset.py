"""A small, hand-authored golden dataset for retrieval-quality evaluation.

Chunks are authored as templates (`EvalChunk`), not real `app.ingestion.schemas.Chunk` objects --
a real `document_id` (and therefore `chunk_id`, which is derived from it) only exists once
`app.evaluation.runner.run_evaluation` stamps these into a fresh ingestion run. Queries reference
their expected chunks by stable `chunk_index`, never a `chunk_id`.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalChunk:
    """One chunk template within an `EvalDocument`."""

    index: int
    section_path: list[str]
    text: str
    page: int = 1


@dataclass(frozen=True)
class EvalDocument:
    """One document template in the golden dataset."""

    label: str
    chunks: list[EvalChunk]


@dataclass(frozen=True)
class EvalQuery:
    """One golden query, naming its target document and the chunk indices that answer it."""

    query: str
    document_label: str
    expected_chunk_indices: list[int]


GOLDEN_DOCUMENTS: list[EvalDocument] = [
    EvalDocument(
        label="cats",
        chunks=[
            EvalChunk(
                index=0,
                section_path=["Cats", "Diet"],
                text=(
                    "Cats are obligate carnivores and need a diet rich in animal protein "
                    "such as meat and fish."
                ),
            ),
            EvalChunk(
                index=1,
                section_path=["Cats", "Behavior"],
                text=(
                    "Cats often groom themselves for hours and are most active during "
                    "dawn and dusk, a behavior pattern called crepuscular."
                ),
            ),
            EvalChunk(
                index=2,
                section_path=["Cats", "Habitat"],
                text="Domestic cats can adapt to apartments as well as houses with outdoor access.",
            ),
        ],
    ),
    EvalDocument(
        label="dogs",
        chunks=[
            EvalChunk(
                index=0,
                section_path=["Dogs", "Training"],
                text=(
                    "Dogs respond well to positive reinforcement training methods "
                    "such as treats and praise."
                ),
            ),
            EvalChunk(
                index=1,
                section_path=["Dogs", "Diet"],
                text="Dogs are omnivores and can eat a balanced mix of meat, vegetables, and grains.",
            ),
        ],
    ),
]

GOLDEN_QUERIES: list[EvalQuery] = [
    EvalQuery(query="What do cats eat?", document_label="cats", expected_chunk_indices=[0]),
    EvalQuery(query="How are dogs trained?", document_label="dogs", expected_chunk_indices=[0]),
    EvalQuery(query="When are cats most active?", document_label="cats", expected_chunk_indices=[1]),
    EvalQuery(query="What can dogs eat?", document_label="dogs", expected_chunk_indices=[1]),
]
