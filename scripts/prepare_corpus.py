#!/usr/bin/env python3
"""Collect a reproducible RCSB PDB protein corpus and build ESM-2 vectors."""

from __future__ import annotations

import argparse
from datetime import date
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from protein_explorer.search import (  # noqa: E402
    MODEL_NAME, MODEL_REVISION, embed_sequences, make_index_manifest,
)

SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
GRAPHQL_URL = "https://data.rcsb.org/graphql"
PDB_URL = "https://files.rcsb.org/download/{entry}.pdb"
AA = set("ACDEFGHIKLMNPQRSTVWY")
# Stable reference entries deliberately come first when they satisfy the query.
SEED_ENTITIES = ("1UBQ_1", "1CRN_1", "1VII_1", "1BTA_1", "2CI2_1")


def request_json(url: str, payload: dict[str, Any], retries: int = 4) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(retries):
        try:
            req = Request(url, data=data, headers={"Content-Type": "application/json",
                         "Accept": "application/json", "User-Agent": "Protein-Explorer/0.1"})
            with urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
            if "errors" in result:
                raise RuntimeError(f"RCSB GraphQL error: {result['errors']}")
            return result
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt + 1 == retries:
                raise RuntimeError(f"RCSB request failed after {retries} attempts: {exc}") from exc
            time.sleep(min(2 ** attempt, 8))
    raise AssertionError("unreachable")


def query_entity_ids(limit: int) -> list[str]:
    query = {"query": {"type": "group", "logical_operator": "and", "nodes": [
        {"type": "terminal", "service": "text", "parameters": {
            "attribute": "entity_poly.rcsb_entity_polymer_type", "operator": "exact_match", "value": "Protein"}},
        {"type": "terminal", "service": "text", "parameters": {
            "attribute": "entity_poly.rcsb_sample_sequence_length", "operator": "range",
            "value": {"from": 30, "to": 300, "include_lower": True, "include_upper": True}}},
    ]}, "return_type": "polymer_entity", "request_options": {
        "paginate": {"start": 0, "rows": 1000},
        "results_verbosity": "minimal",
        "sort": [{"sort_by": "rcsb_accession_info.initial_release_date", "direction": "desc"}],
    }}
    ids = []
    target = max(limit * 5, 1000)
    for start in range(0, target, 1000):
        query["request_options"]["paginate"]["start"] = start
        page = request_json(SEARCH_URL, query)
        page_ids = [row["identifier"] if isinstance(row, dict) else row
                    for row in page.get("result_set", [])]
        ids.extend(page_ids)
        if len(page_ids) < 1000 or len(ids) >= target:
            break
    return ids


def _gql_quote(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(f"Unsafe RCSB entity ID: {value!r}")
    return '"' + value + '"'


def query_entities(entity_ids: list[str]) -> list[dict[str, Any]]:
    if not entity_ids:
        return []
    ids = ",".join(_gql_quote(value) for value in entity_ids)
    query = """query { polymer_entities(entity_ids: [%s]) {
      rcsb_id entity_poly { pdbx_seq_one_letter_code_can rcsb_sample_sequence_length }
      rcsb_polymer_entity { pdbx_description }
      rcsb_polymer_entity_container_identifiers { entry_id entity_id auth_asym_ids }
      rcsb_entity_source_organism { ncbi_scientific_name }
      uniprots { rcsb_uniprot_container_identifiers { uniprot_id } }
    } }""" % ids
    result = request_json(GRAPHQL_URL, {"query": query})
    entities = result.get("data", {}).get("polymer_entities")
    return entities if isinstance(entities, list) else []


def query_methods(entry_ids: list[str]) -> dict[str, Any]:
    # Aliased entry lookups keep this to one small metadata request per page.
    aliases = []
    for entry_id in entry_ids:
        if not re.fullmatch(r"[A-Za-z0-9]{4}", entry_id):
            continue
        # GraphQL names cannot start with a digit (PDB IDs usually do).
        aliases.append(f'e_{entry_id.lower()}: entry(entry_id: "{entry_id}") {{ rcsb_entry_info {{ experimental_method }} }}')
    if not aliases:
        return {}
    result = request_json(GRAPHQL_URL, {"query": "query { " + " ".join(aliases) + " }"})
    methods = {}
    for key, data in (result.get("data") or {}).items():
        if key.startswith("e_"):
            methods[key[2:].upper()] = (data or {}).get("rcsb_entry_info", {}).get("experimental_method") or []
    return methods


def records_from_entities(entities: list[dict[str, Any]], methods: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        identifiers = entity.get("rcsb_polymer_entity_container_identifiers") or {}
        entry = str(identifiers.get("entry_id", "")).upper()
        entity_id = str(identifiers.get("entity_id", ""))
        sequence = (entity.get("entity_poly") or {}).get("pdbx_seq_one_letter_code_can") or ""
        sequence = re.sub(r"\s+", "", sequence).upper()
        chains = identifiers.get("auth_asym_ids") or []
        chain_id = str(chains[0]) if chains else ""
        # The lazy downloader serves legacy PDB text, whose auth chain field is one character.
        if (not re.fullmatch(r"[A-Z0-9]{4}", entry) or not entity_id or not chains
                or len(chain_id) != 1 or not chain_id.strip() or not chain_id.isprintable()
                or not 30 <= len(sequence) <= 300 or not set(sequence) <= AA):
            continue
        method = methods.get(entry, [])
        if not method:
            continue
        name = (entity.get("rcsb_polymer_entity") or {}).get("pdbx_description") or "Unknown protein"
        organisms = entity.get("rcsb_entity_source_organism") or []
        organism = next((x.get("ncbi_scientific_name") for x in organisms if x.get("ncbi_scientific_name")), None)
        uniprots = entity.get("uniprots") or []
        accession = next((x.get("rcsb_uniprot_container_identifiers", {}).get("uniprot_id")
                          for x in uniprots if x.get("rcsb_uniprot_container_identifiers", {}).get("uniprot_id")), None)
        record_id = f"{entry}_{entity_id}"
        records.append({"id": record_id, "name": name, "organism": organism or "Unknown",
                        "sequence": sequence, "structure_url": PDB_URL.format(entry=entry),
                        "structure_type": "experimental", "chain_id": chain_id,
                        "source_url": f"https://www.rcsb.org/structure/{entry}",
                        "license": "CC0 1.0 (PDB archive data)", "accession": accession,
                        "entry_id": entry, "entity_id": entity_id, "experimental_method": method,
                        "collected_on": date.today().isoformat()})
    return records


def collect_corpus(limit: int) -> list[dict[str, Any]]:
    ids = list(SEED_ENTITIES)
    ids.extend(query_entity_ids(limit))
    seen_ids = set()
    ordered_ids = []
    for item in ids:
        if item not in seen_ids:
            seen_ids.add(item)
            ordered_ids.append(item)
    records: list[dict[str, Any]] = []
    sequence_seen = set()
    for start in range(0, len(ordered_ids), 50):
        batch_ids = ordered_ids[start : start + 50]
        fetched = query_entities(batch_ids)
        by_id = {entity.get("rcsb_id"): entity for entity in fetched if isinstance(entity, dict)}
        # RCSB may return entities in its own order, so restore seed and search order.
        entities = [by_id[entity_id] for entity_id in batch_ids if entity_id in by_id]
        entries = sorted({str((x.get("rcsb_polymer_entity_container_identifiers") or {}).get("entry_id", "")).upper()
                          for x in entities})
        methods = query_methods(entries)
        for record in records_from_entities(entities, methods):
            if record["sequence"] in sequence_seen:
                continue
            sequence_seen.add(record["sequence"])
            records.append(record)
            if len(records) >= limit:
                return records
        print(f"RCSB metadata: {min(start + len(batch_ids), len(ordered_ids))}/{len(ordered_ids)} candidates; {len(records)} unique proteins",
              file=sys.stderr, flush=True)
    return records


def prepare(output: Path, limit: int, batch_size: int) -> int:
    records = collect_corpus(limit)
    if not records:
        raise RuntimeError("RCSB returned no eligible unique protein sequences")
    print(f"Collected {len(records)} unique proteins; embedding with CPU ESM-2 in batches of {batch_size}.",
          file=sys.stderr, flush=True)
    vectors = embed_sequences([row["sequence"] for row in records], batch_size=batch_size)
    if vectors.ndim != 2 or vectors.shape[0] != len(records) or not np.isfinite(vectors).all():
        raise RuntimeError("ESM-2 returned an invalid embedding matrix")
    output.mkdir(parents=True, exist_ok=True)
    (output / "corpus.json").write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    np.save(output / "embeddings.npy", vectors.astype(np.float32))
    manifest = make_index_manifest(records, vectors)
    manifest.update({"created_on": date.today().isoformat(), "source": "RCSB PDB Search and Data APIs",
                     "source_url": "https://www.rcsb.org/", "license": "CC0 1.0 (PDB archive data)"})
    (output / "index.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory for corpus.json and embeddings.npy")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args(argv)
    if args.limit < 1 or args.batch_size < 1:
        parser.error("--limit and --batch-size must be positive")
    try:
        count = prepare(args.output.expanduser(), args.limit, args.batch_size)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Corpus preparation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Prepared {count} unique proteins with {MODEL_NAME} ({MODEL_REVISION}) at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
