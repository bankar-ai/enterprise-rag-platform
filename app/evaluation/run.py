"""CLI entrypoint: `uv run python -m app.evaluation.run`.

Runs the retrieval-quality evaluation harness and prints a report. This module's stdout
report output is a deliberate, documented exception to the no-`print()` rule (a CLI tool's
whole purpose is stdout output) -- not a violation to fix.
"""

from app.evaluation.runner import run_evaluation


def main() -> None:
    """Run the evaluation harness and print a summary report."""
    summary = run_evaluation()

    print(f"Evaluation run: {summary.num_queries} queries, top_k={summary.top_k}")
    print(f"  Mean Precision@{summary.top_k}: {summary.mean_precision:.3f}")
    print(f"  Mean Recall@{summary.top_k}:    {summary.mean_recall:.3f}")
    print(f"  MRR:                    {summary.mrr:.3f}")
    print()
    for result in summary.per_query:
        print(f"  [{result.reciprocal_rank:.2f} RR] {result.query!r}")
        print(f"      retrieved: {result.retrieved_chunk_ids}")
        print(f"      relevant:  {result.relevant_chunk_ids}")


if __name__ == "__main__":
    main()
