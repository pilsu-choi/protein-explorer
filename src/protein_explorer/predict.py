"""Safe synchronous invocation of an installed ColabFold batch executable."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from .sequence import parse_sequence


class PredictionError(RuntimeError):
    """Prediction failed or did not produce a valid ranked result."""


def _atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _validate_outputs(output_dir: Path, sequence: str) -> tuple[Path, Path, dict]:
    pdbs = sorted(output_dir.glob("*rank_001*.pdb"))
    scores = sorted(output_dir.glob("*rank_001*.json"))
    if not pdbs or not scores:
        raise PredictionError("ColabFold finished without a rank_001 PDB and matching scores JSON.")
    def rank_suffix(path: Path) -> str:
        return path.stem.split("rank_001", 1)[-1]

    pairs = [(p, s) for p in pdbs for s in scores if rank_suffix(p) == rank_suffix(s)]
    pdb, score_path = pairs[0] if pairs else (None, None)
    if pdb is None or score_path is None:
        raise PredictionError("Could not pair the rank_001 PDB with its scores JSON.")
    try:
        pdb_text = pdb.read_text(encoding="utf-8", errors="replace")
        score_data = json.loads(score_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PredictionError(f"Could not read ranked output: {exc}") from exc
    atom_lines = [line for line in pdb_text.splitlines() if line.startswith(("ATOM  ", "HETATM"))]
    ca_atoms = [line for line in atom_lines if line[12:16].strip() == "CA"]
    if not ca_atoms:
        raise PredictionError("Ranked PDB is empty or has no alpha-carbon atom records.")
    residues = {(line[21:22], line[22:27]) for line in ca_atoms}
    plddt = score_data.get("plddt") if isinstance(score_data, dict) else None
    if not isinstance(plddt, list) or not plddt:
        raise PredictionError("Ranked scores JSON has no non-empty plddt array.")
    if len(plddt) != len(sequence):
        raise PredictionError(f"Ranked scores contain {len(plddt)} plddt values for a {len(sequence)}-residue input.")
    if len(residues) != len(sequence):
        raise PredictionError(f"Ranked PDB has {len(residues)} alpha-carbon residues for a {len(sequence)}-residue input.")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 100 for value in plddt):
        raise PredictionError("Ranked scores JSON contains invalid plddt values; expected numbers from 0 to 100.")
    return pdb, score_path, {"count": len(plddt), "min": min(plddt), "mean": sum(plddt) / len(plddt), "max": max(plddt)}


def _fresh_output_dir(requested: str | None) -> Path:
    if requested is None:
        root = Path("runs")
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"prediction-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    else:
        path = Path(requested)
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise PredictionError(f"Output directory already exists; choose a fresh path: {path}") from exc
    except OSError as exc:
        raise PredictionError(f"Cannot create output directory {path}: {exc}") from exc
    return path


def run_prediction(sequence_text: str, executable: str = "colabfold_batch", output_dir: str | None = None,
                   timeout: int = 7200, num_recycle: int = 3, model_type: str = "alphafold2_ptm",
                   msa_mode: str = "mmseqs2_uniref_env", num_models: int = 5,
                   data_dir: str | None = None) -> tuple[Path, dict]:
    sequence = parse_sequence(sequence_text)
    if not 1 <= timeout <= 86400:
        raise PredictionError("Timeout must be between 1 and 86400 seconds.")
    if not 0 <= num_recycle <= 20:
        raise PredictionError("--num-recycle must be between 0 and 20.")
    if not 1 <= num_models <= 5:
        raise PredictionError("--num-models must be between 1 and 5.")
    if model_type != "alphafold2_ptm":
        raise PredictionError("--model-type currently supports alphafold2_ptm only.")
    if msa_mode not in {"mmseqs2_uniref_env", "single_sequence"}:
        raise PredictionError("--msa-mode must be mmseqs2_uniref_env or single_sequence.")
    resolved = shutil.which(executable)
    if resolved is None:
        raise PredictionError(f"ColabFold executable not found: {executable}. Install it in the prediction environment or pass --colabfold-batch PATH.")
    directory = _fresh_output_dir(output_dir)
    fasta = directory / "query.fasta"
    fasta.write_text(f">protein-explorer\n{sequence}\n", encoding="utf-8")
    log_path = directory / "colabfold.log"
    manifest_path = directory / "manifest.json"
    digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
    argv = [resolved, str(fasta), str(directory), "--model-type", model_type, "--msa-mode", msa_mode,
            "--num-recycle", str(num_recycle), "--num-models", str(num_models)]
    if data_dir is not None:
        argv.extend(("--data", data_dir))
    manifest = {
        "status": "running", "sequence_sha256": digest, "sequence_length": len(sequence),
        "settings": {"executable": resolved, "model_type": model_type, "msa_mode": msa_mode,
                     "num_recycle": num_recycle, "num_models": num_models, "data_dir": data_dir,
                     "timeout_seconds": timeout, "argv": argv},
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "log": log_path.name, "returncode": None,
    }
    _atomic_json(manifest_path, manifest)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=timeout, check=False, shell=False)
        manifest["returncode"] = result.returncode
        _atomic_json(manifest_path, manifest)
        if result.returncode != 0:
            raise PredictionError(f"ColabFold exited with status {result.returncode}; see {log_path}.")
        pdb, scores, summary = _validate_outputs(directory, sequence)
        manifest.update({"status": "succeeded", "pdb": pdb.name, "scores": scores.name,
                         "plddt": summary, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        _atomic_json(manifest_path, manifest)
        return directory, manifest
    except subprocess.TimeoutExpired:
        message = f"ColabFold exceeded the {timeout}s timeout; see {log_path}."
        manifest.update({"status": "failed", "error": message,
                         "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        _atomic_json(manifest_path, manifest)
        raise PredictionError(message)
    except KeyboardInterrupt:
        message = "Prediction interrupted by user."
        manifest.update({"status": "failed", "error": message,
                         "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        _atomic_json(manifest_path, manifest)
        raise
    except (PredictionError, OSError) as exc:
        message = str(exc)
        manifest.update({"status": "failed", "error": message,
                         "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        _atomic_json(manifest_path, manifest)
        if isinstance(exc, PredictionError):
            raise
        raise PredictionError(f"Could not run ColabFold: {exc}. See {log_path}.") from exc
