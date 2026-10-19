import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from protein_explorer.jobs import JobStore
from protein_explorer.worker import _run_one, _software_fingerprint, main as worker_main

SEQ = "ACDEFGHIKLMNPQRSTVWY" * 2


def valid_outputs(directory: Path, sequence: str = SEQ) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pdb = "".join(
        f"ATOM  {i:5d}  CA  ALA A{i:4d}    10.000  10.000  10.000  1.00 90.00           C\n"
        for i in range(1, len(sequence) + 1)
    )
    (directory / "sample_unrelaxed_rank_001.pdb").write_text(pdb, encoding="utf-8")
    (directory / "sample_scores_rank_001.json").write_text(
        json.dumps({"plddt": [90] * len(sequence)}), encoding="utf-8"
    )


class JobStoreTests(unittest.TestCase):
    def test_concurrent_submits_deduplicate_and_claim_is_atomic(self):
        with tempfile.TemporaryDirectory() as temp:
            store = JobStore(temp)
            barrier = threading.Barrier(8)
            submitted = []

            def submit():
                barrier.wait()
                submitted.append(store.submit(SEQ.lower(), {"num_models": 1}))

            threads = [threading.Thread(target=submit) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len({item["id"] for item in submitted}), 1)
            self.assertEqual(sum(not item["cache_hit"] for item in submitted), 1)

            claimed = []
            barrier = threading.Barrier(8)

            def claim():
                barrier.wait()
                claimed.append(store.claim())

            threads = [threading.Thread(target=claim) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sum(item is not None for item in claimed), 1)
            self.assertEqual(store.get(submitted[0]["id"])["status"], "running")

    def test_valid_cached_outputs_reuse_but_missing_or_invalid_outputs_do_not(self):
        with tempfile.TemporaryDirectory() as temp:
            store = JobStore(temp)
            job = store.submit(SEQ, {"num_models": 1})
            self.assertEqual(store.claim()["id"], job["id"])
            out = Path(temp) / "runs" / "cached"
            valid_outputs(out)
            store.complete(job["id"], str(out), {
                "status": "succeeded",
                "pdb": "sample_unrelaxed_rank_001.pdb",
                "scores": "sample_scores_rank_001.json",
            })
            cached = store.submit(SEQ, {"num_models": 1, "timeout": 60})
            self.assertEqual(cached["id"], job["id"])
            self.assertTrue(cached["cache_hit"])
            saved_manifest = json.loads((out / "manifest.json").read_text())
            self.assertIn("artifacts_sha256", saved_manifest)

            out.joinpath("sample_scores_rank_001.json").write_text(
                json.dumps({"plddt": [80] * len(SEQ)}), encoding="utf-8"
            )
            replacement = store.submit(SEQ, {"num_models": 1})
            self.assertNotEqual(replacement["id"], job["id"])
            self.assertEqual(replacement["status"], "queued")

    def test_cache_identity_includes_engine_and_inference_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            store = JobStore(temp)
            first = store.submit(SEQ, {"num_models": 1}, engine_version="weights-a")
            self.assertNotEqual(first["id"], store.submit(SEQ, {"num_models": 2}, "weights-a")["id"])
            self.assertNotEqual(first["id"], store.submit(SEQ, {"num_models": 1}, "weights-b")["id"])
            with self.assertRaisesRegex(ValueError, "Unknown prediction setting"):
                store.submit(SEQ, {"data_dir": "/weights"})

    def test_recovery_marks_stale_running_rows_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = JobStore(temp)
            job = store.submit(SEQ, {})
            store.claim()
            self.assertEqual(store.recover(), 1)
            recovered = store.get(job["id"])
            self.assertEqual(recovered["status"], "failed")
            self.assertIn("stopped", recovered["error"])
            self.assertIsNone(store.get("not-a-uuid"))

    def test_claim_can_select_a_specific_queued_job(self):
        with tempfile.TemporaryDirectory() as temp:
            store = JobStore(temp)
            oldest = store.submit(SEQ, {"num_models": 1})
            target = store.submit("C" + SEQ[1:], {"num_models": 1})
            self.assertEqual(store.claim(target["id"])["id"], target["id"])
            self.assertEqual(store.claim()["id"], oldest["id"])

    def test_bootstrap_fingerprint_is_rejected_when_software_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = JobStore(temp)
            bootstrap = {
                "available": True, "version": "test", "fingerprint": "provisional",
                "packages": {"colabfold": "1"}, "weights": {"download_pending": True},
            }
            changed = {
                "available": True, "version": "test", "fingerprint": "new-engine",
                "packages": {"colabfold": "2"}, "weights": {"download_pending": True},
            }
            job = store.submit(SEQ, {"num_models": 1}, engine_version="provisional")
            with patch("protein_explorer.worker.run_prediction") as predict:
                self.assertTrue(_run_one(
                    store, "unused", None, {}, changed,
                    [{"fingerprint": "provisional",
                      "software_fingerprint": _software_fingerprint(bootstrap)}],
                ))
            self.assertEqual(store.get(job["id"])["status"], "failed")
            predict.assert_not_called()

    def test_worker_once_runs_fake_executable_and_persists_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = JobStore(root)
            identity = {"available": True, "version": "test", "fingerprint": "test-fingerprint",
                        "packages": {"colabfold": "test"}, "weights": {"download_pending": True}}
            after_download = {"available": True, "version": "test", "fingerprint": "weights-downloaded",
                              "packages": {"colabfold": "test"}, "weights": {"params.npz": "abc123"}}
            job = store.submit(SEQ, {"num_models": 1}, engine_version=identity["fingerprint"])
            second_job = store.submit("C" + SEQ[1:], {"num_models": 1},
                                      engine_version=identity["fingerprint"])
            executable = root / "fake colabfold"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "out = pathlib.Path(sys.argv[2])\n"
                "seq = pathlib.Path(sys.argv[1]).read_text().splitlines()[1]\n"
                "pdb = ''.join(f'ATOM  {i:5d}  CA  ALA A{i:4d}    10.000  10.000  10.000  1.00 90.00           C\\n' for i in range(1, len(seq)+1))\n"
                "(out / 'q_unrelaxed_rank_001.pdb').write_text(pdb)\n"
                "(out / 'q_scores_rank_001.json').write_text(json.dumps({'plddt':[90]*len(seq)}))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            with patch("protein_explorer.runtime.engine_identity",
                       side_effect=[identity, identity, after_download,
                                    after_download, after_download, after_download]):
                self.assertEqual(worker_main(["--root", temp, "--colabfold-batch", str(executable),
                                              "--data-dir", str(root / "weights"), "--once"]), 0)
                self.assertEqual(worker_main(["--root", temp, "--colabfold-batch", str(executable),
                                              "--data-dir", str(root / "weights"), "--once"]), 0)
            finished = store.get(job["id"])
            second_finished = store.get(second_job["id"])
            self.assertEqual(finished["status"], "succeeded")
            self.assertEqual(second_finished["status"], "succeeded")
            self.assertEqual(finished["engine_version"], after_download["fingerprint"])
            self.assertEqual(second_finished["engine_version"], after_download["fingerprint"])
            self.assertTrue(Path(finished["output_dir"]).joinpath(finished["manifest"]["pdb"]).exists())
            result_manifest = json.loads(
                (Path(finished["output_dir"]) / "manifest.json").read_text()
            )
            self.assertEqual(result_manifest["engine_fingerprint"], after_download["fingerprint"])
            self.assertIn("artifacts_sha256", result_manifest)
            self.assertTrue((Path(finished["output_dir"]) / "engine.json").exists())
            self.assertFalse((root / "worker-heartbeat.json").exists())
            self.assertEqual(json.loads((root / "engine.json").read_text())["fingerprint"],
                             after_download["fingerprint"])
            self.assertEqual(
                json.loads((root / "engine.json").read_text())["bootstrap_fingerprints"][0]["fingerprint"],
                identity["fingerprint"],
            )


if __name__ == "__main__":
    unittest.main()
