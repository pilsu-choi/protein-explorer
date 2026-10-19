"""Single-host ColabFold queue worker."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time

for _key, _value in {
    "JAX_PLATFORMS": "cuda,cpu",
    "TF_FORCE_UNIFIED_MEMORY": "0",
    "XLA_PYTHON_CLIENT_PREALLOCATE": "false",
    "XLA_PYTHON_CLIENT_MEM_FRACTION": ".5",
    "OMP_NUM_THREADS": "4",
    "OPENBLAS_NUM_THREADS": "4",
}.items():
    os.environ[_key] = _value

from .jobs import JobStore
from .predict import PredictionError, run_prediction


def _heartbeat(path: Path, stop: threading.Event, state: dict) -> None:
    while not stop.is_set():
        snapshot = {**state, "pid": os.getpid(), "updated_at": time.time()}
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            pass
        stop.wait(5)


def _engine_identity(executable: str, data_dir: str | None) -> dict:
    from .runtime import engine_identity
    identity = engine_identity(executable, data_dir)
    if (not isinstance(identity, dict) or not isinstance(identity.get("fingerprint"), str)
            or not isinstance(identity.get("available"), bool)):
        raise RuntimeError("runtime.engine_identity must return available and fingerprint fields")
    return identity


def _software_fingerprint(identity: dict) -> str:
    software = {key: value for key, value in identity.items()
                if key not in {"available", "fingerprint", "weights"}}
    return hashlib.sha256(json.dumps(software, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _is_pending_weights(identity: dict) -> bool:
    weights = identity.get("weights")
    return isinstance(weights, dict) and weights.get("download_pending") is True


def _bootstrap_fingerprints(root: Path, identity: dict) -> list[dict]:
    software = _software_fingerprint(identity)
    entries = []
    try:
        stored = json.loads((root / "engine.json").read_text(encoding="utf-8"))
        entries = [entry for entry in stored.get("bootstrap_fingerprints", [])
                   if isinstance(entry, dict) and entry.get("software_fingerprint") == software
                   and isinstance(entry.get("fingerprint"), str)]
    except (OSError, ValueError, AttributeError):
        pass
    if _is_pending_weights(identity):
        entry = {"fingerprint": identity["fingerprint"], "software_fingerprint": software}
        if entry not in entries:
            entries.append(entry)
    return entries


def _publish_engine(root: Path, identity: dict, bootstrap_fingerprints: list[dict] | None = None) -> None:
    path = root / "engine.json"
    temporary = path.with_suffix(".json.tmp")
    document = {**identity, "bootstrap_fingerprints": bootstrap_fingerprints or []}
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sigterm_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


def _run_one(store: JobStore, executable: str, data_dir: str | None, state: dict,
             identity: dict, bootstrap_fingerprints: list[dict]) -> bool:
    job = store.claim()
    if job is None:
        return False
    state.update(status="running", job_id=job["id"])
    allowed_bootstrap = any(
        item["fingerprint"] == job["engine_version"]
        and item["software_fingerprint"] == _software_fingerprint(identity)
        for item in bootstrap_fingerprints
    )
    if job["engine_version"] != identity["fingerprint"] and not allowed_bootstrap:
        store.fail(job["id"], "Queued job engine fingerprint does not match the active worker.")
        return True
    out = store.root / "runs" / job["id"] / "attempt-1"
    settings = job["settings"]
    try:
        directory, manifest = run_prediction(
            job["sequence"], executable=str(executable), output_dir=str(out),
            timeout=settings["timeout"], num_recycle=settings["num_recycle"],
            model_type=settings["model_type"], msa_mode=settings["msa_mode"],
            num_models=settings["num_models"], data_dir=data_dir,
        )
        completed_identity = _engine_identity(executable, data_dir)
        _publish_engine(store.root, completed_identity, bootstrap_fingerprints)
        _publish_engine(directory, completed_identity, bootstrap_fingerprints)
        state["engine_fingerprint"] = completed_identity["fingerprint"]
        if not completed_identity["available"]:
            store.fail(job["id"], "Prediction finished, but the active engine could not be fingerprinted.")
        else:
            store.complete(job["id"], str(directory), manifest, completed_identity["fingerprint"])
    except KeyboardInterrupt:
        store.fail(job["id"], "Worker interrupted during prediction.")
        raise
    except (PredictionError, OSError, ValueError) as exc:
        store.fail(job["id"], str(exc))
    except Exception as exc:
        store.fail(job["id"], f"Unexpected worker error: {exc}")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run queued Protein Explorer predictions.")
    parser.add_argument("--root", required=True, help="job database and result root")
    parser.add_argument("--colabfold-batch", required=True, help="ColabFold batch executable")
    parser.add_argument("--data-dir", required=True, help="ColabFold model cache directory")
    parser.add_argument("--once", action="store_true", help="process at most one queued job and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = JobStore(args.root)
    lock_path = store.root / "worker.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A worker already holds the prediction lock.")
            return 1

        try:
            identity = _engine_identity(args.colabfold_batch, args.data_dir)
        except (OSError, RuntimeError) as exc:
            print(f"Cannot inspect the prediction engine: {exc}")
            return 2
        bootstrap_fingerprints = _bootstrap_fingerprints(store.root, identity)
        _publish_engine(store.root, identity, bootstrap_fingerprints)
        if not identity["available"]:
            print(f"Prediction engine unavailable: {identity.get('error', 'install ColabFold and its model environment')}")
            return 2
        store.recover()
        heartbeat = store.root / "worker-heartbeat.json"
        state = {"status": "idle", "job_id": None,
                 "engine_fingerprint": identity["fingerprint"]}
        stop = threading.Event()
        thread = threading.Thread(target=_heartbeat, args=(heartbeat, stop, state), daemon=True)
        thread.start()
        previous_sigterm = signal.signal(signal.SIGTERM, _sigterm_interrupt)
        try:
            while True:
                try:
                    identity = _engine_identity(args.colabfold_batch, args.data_dir)
                except (OSError, RuntimeError) as exc:
                    print(f"Cannot inspect the prediction engine: {exc}")
                    return 2
                _publish_engine(store.root, identity, bootstrap_fingerprints)
                if not identity["available"]:
                    print(f"Prediction engine unavailable: {identity.get('error', 'install ColabFold and its model environment')}")
                    return 2
                state["engine_fingerprint"] = identity["fingerprint"]
                state.update(status="idle", job_id=None)
                did_work = _run_one(store, args.colabfold_batch, args.data_dir, state,
                                    identity, bootstrap_fingerprints)
                if args.once:
                    final_job = store.get(state["job_id"]) if state.get("job_id") else None
                    return 1 if final_job and final_job["status"] == "failed" else 0
                state.update(status="idle", job_id=None)
                if not did_work:
                    time.sleep(2)
        except KeyboardInterrupt:
            return 130
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
            stop.set()
            thread.join(timeout=6)
            try:
                heartbeat.unlink()
            except FileNotFoundError:
                pass
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    raise SystemExit(main())
