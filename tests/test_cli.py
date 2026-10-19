import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from protein_explorer.cli import main
from protein_explorer.predict import run_prediction
from protein_explorer.sequence import SequenceError, parse_sequence, read_sequence

SEQ = "ACDEFGHIKLMNPQRSTVWY" * 2


class SequenceTests(unittest.TestCase):
    def test_raw_and_fasta_normalize(self):
        self.assertEqual(parse_sequence(" acdefghiklmnpqrstvwy\nacdefghiklmnpqrstvwy "), SEQ)
        self.assertEqual(parse_sequence(">sample description\n" + SEQ.lower()), SEQ)

    def test_rejects_multiple_records_invalid_characters_and_boundaries(self):
        for value in (">one\n" + SEQ + "\n>two\n" + SEQ, SEQ[:-1] + "X", "A" * 29, "A" * 301):
            with self.subTest(value=value[:20]), self.assertRaises(SequenceError):
                parse_sequence(value)

    def test_reads_fasta_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.fa"
            path.write_text(">q\n" + SEQ, encoding="utf-8")
            self.assertEqual(read_sequence(None, str(path)), SEQ)


class PredictionTests(unittest.TestCase):
    def make_executable(self, directory: Path, body: str) -> Path:
        path = directory / "fake colabfold; harmless"
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    @staticmethod
    def success_script():
        return '''import json, pathlib, sys
out = pathlib.Path(sys.argv[2])
sequence = pathlib.Path(sys.argv[1]).read_text().splitlines()[1]
pdb = "".join(f"ATOM  {i:5d}  CA  ALA A{i:4d}    10.000  10.000  10.000  1.00 90.00           C\\n" for i in range(1, len(sequence) + 1)) + "END\\n"
(out / "query_unrelaxed_rank_001_alphafold2_ptm_model_1_seed_000.pdb").write_text(pdb)
(out / "query_scores_rank_001_alphafold2_ptm_model_1_seed_000.json").write_text(json.dumps({"plddt": [89.5] * len(sequence)}))
'''

    def test_success_writes_manifest_and_checks_real_named_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = self.make_executable(root, self.success_script())
            data_dir = str(root / "weights cache")
            output, manifest = run_prediction(SEQ, str(executable), str(root / "run"), timeout=10,
                                              num_models=1, data_dir=data_dir)
            self.assertEqual(manifest["status"], "succeeded")
            self.assertEqual(manifest["plddt"]["mean"], 89.5)
            self.assertEqual(manifest["returncode"], 0)
            self.assertEqual(manifest["settings"]["num_models"], 1)
            self.assertEqual(manifest["settings"]["data_dir"], data_dir)
            self.assertEqual(manifest["settings"]["argv"][-4:], ["--num-models", "1", "--data", data_dir])
            self.assertTrue((output / manifest["pdb"]).exists())
            stored = json.loads((output / "manifest.json").read_text())
            self.assertEqual(stored["sequence_sha256"], manifest["sequence_sha256"])

    def test_argument_is_passed_as_one_argv_item_and_output_must_be_fresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = self.make_executable(root, self.success_script())
            # The executable path contains spaces and shell punctuation; direct argv execution handles it safely.
            output, manifest = run_prediction(SEQ, str(executable), str(root / "new output"), timeout=10)
            argv = manifest["settings"]["argv"]
            self.assertEqual(argv[-2:], ["--num-models", "5"])
            self.assertNotIn("--data", argv)
            self.assertIsNone(manifest["settings"]["data_dir"])
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                run_prediction(SEQ, str(executable), str(root / "new output"), timeout=10)

    def test_nonzero_exit_missing_and_malformed_output_fail_with_manifest(self):
        cases = (
            ("import sys; sys.exit(7)\n", "status 7"),
            ("pass\n", "without a rank_001 PDB"),
            (self.success_script() + '\nnext(out.glob("*scores*.json")).write_text("invalid JSON")\n', "Could not read ranked output"),
            (self.success_script() + '\nnext(out.glob("*scores*.json")).write_text(json.dumps({"plddt": [90] * 39}))\n', "39 plddt values"),
            (self.success_script() + '\nnext(out.glob("*scores*.json")).write_text(json.dumps({"plddt": [True] * len(sequence)}))\n', "invalid plddt"),
            (self.success_script() + '\np = next(out.glob("*.pdb")); p.write_text(p.read_text().splitlines()[0] + "\\n")\n', "1 alpha-carbon residues"),
            ('''import pathlib, sys
out = pathlib.Path(sys.argv[2])
(out / "x_unrelaxed_rank_001.pdb").write_text("garbage\\n")
(out / "x_scores_rank_001.json").write_text("{}")
''', "no alpha-carbon"),
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, (body, message) in enumerate(cases):
                executable = self.make_executable(root, body)
                output = root / f"run-{index}"
                with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                    run_prediction(SEQ, str(executable), str(output), timeout=10)
                failed_manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(failed_manifest["status"], "failed")
                self.assertIsInstance(failed_manifest["returncode"], int)
                if message == "status 7":
                    self.assertEqual(failed_manifest["returncode"], 7)

    def test_timeout_is_recorded_as_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = self.make_executable(root, "import time; time.sleep(3)\n")
            output = root / "run"
            with self.assertRaisesRegex(RuntimeError, "timeout"):
                run_prediction(SEQ, str(executable), str(output), timeout=1)
            failed_manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(failed_manifest["status"], "failed")
            self.assertIsNone(failed_manifest["returncode"])

    def test_num_models_range_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = self.make_executable(root, self.success_script())
            for count in (0, 6):
                with self.subTest(count=count), self.assertRaisesRegex(RuntimeError, "--num-models"):
                    run_prediction(SEQ, str(executable), str(root / f"run-{count}"), num_models=count)

    def test_cli_num_models_range_is_enforced(self):
        for count in ("0", "6"):
            with self.subTest(count=count), self.assertRaises(SystemExit):
                main(["predict", SEQ, "--num-models", count])

    def test_cli_validates_and_returns_nonzero_for_invalid_input(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["validate", SEQ])
        self.assertEqual(code, 0)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["validate", "A" * 29])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
