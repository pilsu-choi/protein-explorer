#!/usr/bin/env python3
"""Import a real verified CLI result into the local job history."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from protein_explorer.jobs import JobStore
from protein_explorer.predict import _validate_outputs
from protein_explorer.runtime import engine_identity
from protein_explorer.sequence import read_sequence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--colabfold-batch", required=True)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    for key, value in {"JAX_PLATFORMS": "cuda,cpu", "TF_FORCE_UNIFIED_MEMORY": "0",
                       "XLA_PYTHON_CLIENT_PREALLOCATE": "false", "XLA_PYTHON_CLIENT_MEM_FRACTION": ".5"}.items():
        os.environ[key] = value
    source = args.source.resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    sequence = read_sequence(None, str(source / "query.fasta"))
    if manifest.get("status") != "succeeded" or hashlib.sha256(sequence.encode()).hexdigest() != manifest.get("sequence_sha256"):
        parser.error("Source must be a succeeded prediction with a matching input checksum.")
    _validate_outputs(source, sequence)
    identity = engine_identity(args.colabfold_batch, args.data_dir)
    if not identity["available"]:
        parser.error(identity.get("error", "Prediction engine unavailable."))
    settings = {key: manifest["settings"][key] for key in ("num_models", "num_recycle", "model_type", "msa_mode")}
    store = JobStore(args.root)
    with (store.root / "worker.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        job = store.submit(sequence, settings, identity["fingerprint"])
        if job["status"] != "succeeded":
            if store.claim(job["id"]) is None:
                parser.error("Cannot claim the import job.")
            destination = store.root / "runs" / job["id"] / "imported"
            destination.mkdir(parents=True, exist_ok=False)
            names = [manifest["pdb"], manifest["scores"], "manifest.json", "query.fasta",
                     "colabfold.log", "config.json", "environment.json", "validation.json"]
            for name in names:
                if Path(name).name != name:
                    parser.error("Source manifest contains an unsafe filename.")
                if (source / name).is_file():
                    shutil.copy2(source / name, destination / name)
            shutil.copy2(source / "manifest.json", destination / "source-manifest.json")
            if (source / "checksums.json").is_file():
                shutil.copy2(source / "checksums.json", destination / "source-checksums.json")
            (destination / "engine.json").write_text(json.dumps(identity, indent=2) + "\n")
            store.complete(job["id"], str(destination), manifest, identity["fingerprint"])
            checksums = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in destination.iterdir() if path.is_file()}
            (destination / "checksums.json").write_text(json.dumps(checksums, indent=2) + "\n")
        (store.root / "engine.json").write_text(json.dumps(identity, indent=2) + "\n")
        print(json.dumps(store.get(job["id"]), indent=2))


if __name__ == "__main__":
    main()
