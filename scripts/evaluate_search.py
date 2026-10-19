#!/usr/bin/env python3
"""Evaluate available labels, or report exact self-retrieval as a sanity check."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from protein_explorer.search import MODEL_NAME, MODEL_REVISION, embed_sequences  # noqa: E402


def recall_at_k(scores: np.ndarray, relevant_by_query: list[set[int]], top_k: int,
                excluded: list[int] | None = None) -> dict[str, object]:
    """Measure mean relevant-item recall and query-level hit rate separately."""
    if not relevant_by_query:
        return {"queries": 0, "recall_at_k": None}
    excluded = excluded or [-1] * len(relevant_by_query)
    hits = 0
    recall_sum = 0.0
    for query_index, relevant in enumerate(relevant_by_query):
        row = np.asarray(scores[query_index], dtype=np.float64).copy()
        if excluded[query_index] >= 0:
            row[excluded[query_index]] = -np.inf
        order = np.argsort(-row, kind="stable")[:min(top_k, len(row) - (excluded[query_index] >= 0))]
        retrieved = set(map(int, order)) & relevant
        recall_sum += len(retrieved) / len(relevant) if relevant else 0
        if retrieved:
            hits += 1
    return {"queries": len(relevant_by_query), "recall_at_k": recall_sum / len(relevant_by_query),
            "hit_rate_at_k": hits / len(relevant_by_query), "hits": hits, "k": top_k}


def _kmer_jaccard(left: str, right: str, k: int = 3) -> float:
    left_kmers = {left[i:i + k] for i in range(max(0, len(left) - k + 1))}
    right_kmers = {right[i:i + k] for i in range(max(0, len(right) - k + 1))}
    union = left_kmers | right_kmers
    return len(left_kmers & right_kmers) / len(union) if union else 0.0


def evaluate(root: Path, limit: int, batch_size: int) -> dict[str, object]:
    corpus_path = root / "corpus.json"
    records = json.loads(corpus_path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("corpus.json must contain at least one record")
    records = records[:limit]
    vectors = embed_sequences([record["sequence"] for record in records], batch_size=batch_size)
    similarities = vectors @ vectors.T
    n = len(records)
    top_k = min(10, n)
    self_order = np.argsort(-similarities, axis=1, kind="stable")[:, :top_k]
    self_ranks = [int(np.flatnonzero(row == index)[0]) + 1 if index in row else n + 1
                  for index, row in enumerate(self_order)]

    accession_groups: dict[str, list[int]] = defaultdict(list)
    accession_by_index: dict[int, str] = {}
    for index, record in enumerate(records):
        accession = record.get("accession")
        if isinstance(accession, str) and accession.strip():
            normalized = accession.strip()
            accession_groups[normalized].append(index)
            accession_by_index[index] = normalized
    repeated_groups = {label: indexes for label, indexes in accession_groups.items() if len(indexes) > 1}
    grouped_queries = [index for indexes in repeated_groups.values() for index in indexes]
    grouped_relevant = [set(repeated_groups[accession_by_index[index]]) - {index}
                        for index in grouped_queries]
    group_result = None
    baseline_result = None
    if grouped_queries:
        heldout_scores = similarities[grouped_queries].copy()
        group_result = recall_at_k(heldout_scores, grouped_relevant, 10, grouped_queries)
        sequence_scores = np.empty_like(heldout_scores)
        for local_index, query_index in enumerate(grouped_queries):
            sequence_scores[local_index] = [_kmer_jaccard(records[query_index]["sequence"], record["sequence"])
                                            for record in records]
        baseline_result = recall_at_k(sequence_scores, grouped_relevant, 10, grouped_queries)
    return {"evaluation": "exact self retrieval sanity check plus optional same-UniProt retrieval",
            "interpretation": "Same-accession retrieval measures matching UniProt labels only; it is not held-out family generalization. Exact self retrieval is a sanity check, not a quality metric.",
            "model": MODEL_NAME, "revision": MODEL_REVISION, "records": n,
            "self_retrieval": {"top_k": top_k, "hits": sum(rank <= top_k for rank in self_ranks),
                               "recall_at_k": sum(rank <= top_k for rank in self_ranks) / n,
                               "median_rank": float(np.median(self_ranks))},
            "same_uniprot_accession": {"available": bool(grouped_queries),
                                       "repeated_accession_groups": len(repeated_groups),
                                       "queries": len(grouped_queries), "top_k": 10,
                                       "esm2_recall_at_k": group_result,
                                       "three_mer_jaccard_recall_at_k": baseline_result,
                                       "reason_unavailable": None if grouped_queries else
                                       "No UniProt accession occurs more than once after exact-sequence deduplication."}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, help="Prepared corpus directory")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args(argv)
    if args.limit < 1 or args.batch_size < 1:
        parser.error("--limit and --batch-size must be positive")
    try:
        print(json.dumps(evaluate(args.corpus.expanduser(), args.limit, args.batch_size), indent=2))
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
