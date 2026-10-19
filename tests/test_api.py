import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from fastapi.testclient import TestClient

from protein_explorer.api import create_app

SEQUENCE = "ACDEFGHIKLMNPQRSTVWY" * 2


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "engine.json").write_text(json.dumps({"available": True, "version": "test", "fingerprint": "test-only"}))
        self.app = create_app(self.root)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_submit_validate_and_pending_structure(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertFalse(self.client.get("/api/health").json()["corpus"]["ready"])
        self.assertEqual(self.client.post("/api/jobs", json={"sequence": "X" * 40}).status_code, 422)
        self.assertEqual(self.client.post("/api/jobs", json={"sequence": SEQUENCE, "settings": {"executable": "bad"}}).status_code, 422)
        job = self.client.post("/api/jobs", json={"sequence": SEQUENCE}).json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(self.client.get(f'/api/jobs/{job["id"]}/structure').status_code, 409)
        self.assertEqual(self.client.get("/api/jobs/not-a-job").status_code, 404)
        self.assertEqual(self.client.post("/api/search", json={"sequence": SEQUENCE}).status_code, 503)

    def test_completed_result_downloads_and_cache(self):
        job = self.client.post("/api/jobs", json={"sequence": SEQUENCE}).json()
        self.app.state.store.claim(job["id"])
        directory = self.root / "runs" / job["id"] / "test-fixture"
        directory.mkdir(parents=True)
        pdb = "fixture_unrelaxed_rank_001_model.pdb"
        scores = "fixture_scores_rank_001_model.json"
        (directory / pdb).write_text("".join(f"ATOM  {i:5d}  CA  ALA A{i:4d}    10.000  10.000  10.000  1.00 90.00           C\n" for i in range(1, 41)))
        (directory / scores).write_text(json.dumps({"plddt": [90] * 40}))
        manifest = {"status": "succeeded", "pdb": pdb, "scores": scores, "plddt": {"mean": 90}}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        self.app.state.store.complete(job["id"], str(directory), manifest)
        self.assertIn("ATOM", self.client.get(f'/api/jobs/{job["id"]}/structure').text)
        report = self.client.get(f'/api/jobs/{job["id"]}/download?kind=bundle')
        with zipfile.ZipFile(io.BytesIO(report.content)) as archive:
            self.assertEqual(set(archive.namelist()), {pdb, scores, "manifest.json"})
        repeat = self.client.post("/api/jobs", json={"sequence": SEQUENCE}).json()
        self.assertTrue(repeat["cache_hit"])
        self.assertEqual(repeat["id"], job["id"])
        (directory / pdb).unlink()
        repeat = self.client.post("/api/jobs", json={"sequence": SEQUENCE}).json()
        self.assertFalse(repeat["cache_hit"])
        self.assertNotEqual(repeat["id"], job["id"])

    def test_unavailable_engine_does_not_queue_jobs(self):
        (self.root / "engine.json").unlink()
        self.assertEqual(self.client.post("/api/jobs", json={"sequence": SEQUENCE}).status_code, 503)
        self.assertEqual(self.client.get("/api/jobs").json(), [])


if __name__ == "__main__":
    unittest.main()
