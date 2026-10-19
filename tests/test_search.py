import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from protein_explorer.search import ProteinSearch, make_index_manifest
from scripts.evaluate_search import evaluate, recall_at_k
from scripts.prepare_corpus import collect_corpus, query_methods, records_from_entities


def record(protein_id, sequence):
    return {"id": protein_id, "name": protein_id, "organism": "Test species",
            "sequence": sequence, "structure_url": "https://example.test/test.pdb",
            "structure_type": "experimental", "chain_id": "B",
            "source_url": "https://example.test", "license": "CC0 1.0"}


class FakeEncoder:
    def __init__(self, vectors):
        self.vectors = vectors

    def encode(self, sequences, batch_size=32):
        return np.asarray([self.vectors[sequence] for sequence in sequences], dtype=np.float32)


class ProteinSearchTests(unittest.TestCase):
    def write_index(self, root, rows, vectors):
        root.mkdir(parents=True, exist_ok=True)
        (root / "corpus.json").write_text(json.dumps(rows), encoding="utf-8")
        np.save(root / "embeddings.npy", np.asarray(vectors, dtype=np.float32))
        (root / "index.json").write_text(json.dumps(make_index_manifest(rows, np.asarray(vectors))), encoding="utf-8")

    def test_cosine_order_and_result_contract(self):
        rows = [record("parallel", "AAA"), record("near", "BBB"), record("opposite", "CCC")]
        vectors = np.asarray([[1, 0], [0.8, 0.6], [-1, 0]], dtype=np.float32)
        query_vector = {"QUERY": [1, 0]}
        with tempfile.TemporaryDirectory() as temp:
            self.write_index(Path(temp), rows, vectors)
            search = ProteinSearch(temp)
            search._encoder = FakeEncoder(query_vector)
            found = search.search("QUERY", limit=2)
            self.assertEqual([x["id"] for x in found], ["parallel", "near"])
            self.assertEqual(found[0]["score"], 1.0)
            self.assertEqual(found[1]["score"], np.float32(0.8))
            self.assertEqual(found[0]["length"], 3)
            self.assertIn("structure_url", found[0])
            self.assertTrue(search.ready)

    def test_stale_metadata_embedding_and_model_indexes_are_not_ready(self):
        rows = [record("one", "AAA"), record("two", "BBB")]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_index(root, rows, [[1, 0], [0, 1]])
            changed = [dict(rows[0], name="changed"), rows[1]]
            (root / "corpus.json").write_text(json.dumps(changed), encoding="utf-8")
            stale = ProteinSearch(root)
            self.assertFalse(stale.ready)
            self.assertIn("fingerprint", stale.info()["error"])
            manifest = make_index_manifest(rows, np.asarray([[1, 0], [0, 1]], dtype=np.float32))
            manifest["model"] = "another-model"
            (root / "corpus.json").write_text(json.dumps(rows), encoding="utf-8")
            (root / "index.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertFalse(ProteinSearch(root).ready)
            np.save(root / "embeddings.npy", np.asarray([[1, 0]], dtype=np.float32))
            (root / "index.json").write_text(json.dumps(make_index_manifest(rows, np.asarray([[1, 0], [0, 1]]))), encoding="utf-8")
            self.assertFalse(ProteinSearch(root).ready)

    def test_get_rejects_path_like_ids_and_missing_index_reports_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            search = ProteinSearch(temp)
            self.assertFalse(search.ready)
            with self.assertRaisesRegex(RuntimeError, "index files not prepared"):
                search.search("AAA")
            self.assertIsNone(search.get("../corpus"))

    def test_structure_fetch_selects_requested_chain_and_caches_safely(self):
        rows = [record("entry_1", "AAA")]
        with tempfile.TemporaryDirectory() as temp:
            self.write_index(Path(temp), rows, [[1, 0]])
            search = ProteinSearch(temp)
            pdb = ("HEADER    test\n"
                   "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
                   "ATOM      2  CA  GLY B   1       1.000   0.000   0.000  1.00 20.00           C\n"
                   "END\n")
            with patch("protein_explorer.search.urlopen", return_value=__import__("io").BytesIO(pdb.encode())) as fetch:
                selected = search.structure("entry_1")
                cached = search.structure("entry_1")
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(selected, cached)
            self.assertIn("GLY B", selected)
            self.assertNotIn("ALA A", selected)
            with self.assertRaises(KeyError):
                search.structure("../entry_1")

    def test_structure_selection_requires_chain_atoms_and_uses_first_model(self):
        two_models = ("MODEL        1\n"
                      "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
                      "ATOM      2  CA  GLY B   1       1.000   0.000   0.000  1.00 20.00           C\n"
                      "ENDMDL\nMODEL        2\n"
                      "ATOM      3  CA  SER B   1       2.000   0.000   0.000  1.00 20.00           C\n"
                      "ENDMDL\n")
        from protein_explorer.search import _select_pdb_chain
        selected = _select_pdb_chain(two_models, "B")
        self.assertIn("GLY B", selected)
        self.assertNotIn("SER B", selected)
        self.assertEqual(_select_pdb_chain("HEADER test\n", "A"), "")
        self.assertEqual(_select_pdb_chain(two_models, "C"), "")

    def test_rcsb_metadata_aliases_start_with_letter_and_multi_char_chains_are_skipped(self):
        with patch("scripts.prepare_corpus.request_json", return_value={"data": {
                "e_1ubq": {"rcsb_entry_info": {"experimental_method": ["X-RAY DIFFRACTION"]}}}}) as request:
            methods = query_methods(["1UBQ"])
        self.assertIn("e_1ubq", request.call_args.args[1]["query"])
        self.assertEqual(methods, {"1UBQ": ["X-RAY DIFFRACTION"]})
        entity = {"rcsb_polymer_entity_container_identifiers": {
                    "entry_id": "1UBQ", "entity_id": "1", "auth_asym_ids": ["AA"]},
                  "entity_poly": {"pdbx_seq_one_letter_code_can": "A" * 76}}
        self.assertEqual(records_from_entities([entity], {"1UBQ": ["X-RAY DIFFRACTION"]}), [])

    def test_collect_corpus_restores_seed_order_after_graphql_reorders_results(self):
        def entity(entry_id, sequence):
            return {"rcsb_id": f"{entry_id}_1", "rcsb_polymer_entity_container_identifiers": {
                        "entry_id": entry_id, "entity_id": "1", "auth_asym_ids": ["A"]},
                    "entity_poly": {"pdbx_seq_one_letter_code_can": sequence}}
        fetched = [entity("1CRN", "C" * 46), entity("1UBQ", "A" * 76)]
        with patch("scripts.prepare_corpus.query_entity_ids", return_value=[]), \
             patch("scripts.prepare_corpus.query_entities", return_value=fetched), \
             patch("scripts.prepare_corpus.query_methods", return_value={
                 "1UBQ": "X-ray", "1CRN": "X-ray"}):
            records = collect_corpus(2)
        self.assertEqual([row["id"] for row in records], ["1UBQ_1", "1CRN_1"])

    def test_recall_metric_does_not_count_a_missing_relevant_item(self):
        scores = np.asarray([[0.9, 0.8, 0.7]], dtype=np.float32)
        result = recall_at_k(scores, [{2}], top_k=1)
        self.assertEqual(result["hits"], 0)
        self.assertEqual(result["recall_at_k"], 0.0)

    def test_recall_distinguishes_partial_retrieval_from_query_hit_rate(self):
        result = recall_at_k(np.asarray([[1, .9, .8]]), [{1, 2}], 1, excluded=[0])
        self.assertEqual(result["recall_at_k"], 0.5)
        self.assertEqual(result["hit_rate_at_k"], 1.0)

    def test_evaluation_reports_same_accession_baseline_only_when_labels_repeat(self):
        rows = [record("one", "A" * 30), record("two", "C" * 30), record("three", "D" * 30)]
        rows[0]["accession"] = rows[1]["accession"] = "P12345"
        rows[2]["accession"] = None
        vectors = np.asarray([[1, 0], [0.9, 0.1], [0, 1]], dtype=np.float32)
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "corpus.json").write_text(json.dumps(rows), encoding="utf-8")
            with patch("scripts.evaluate_search.embed_sequences", return_value=vectors):
                report = evaluate(Path(temp), 10, 8)
        result = report["same_uniprot_accession"]
        self.assertTrue(result["available"])
        self.assertEqual(result["queries"], 2)
        self.assertEqual(result["esm2_recall_at_k"]["recall_at_k"], 1.0)
        self.assertIsNotNone(result["three_mer_jaccard_recall_at_k"])


if __name__ == "__main__":
    unittest.main()
