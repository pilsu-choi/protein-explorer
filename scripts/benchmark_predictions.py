#!/usr/bin/env python3
"""Measure serial real queue predictions of public reference sequences."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen


def request(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(url, data=data, headers={"Content-Type": "application/json"}), timeout=30) as response:
        return json.load(response)


def gpu_memory():
    try:
        probe = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=5, check=True)
        return int(probe.stdout.strip().splitlines()[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="+", default=["1UBQ_1", "45AR_2", "9ZWZ_1", "13GK_1"])
    args = parser.parse_args()
    health = request(args.url + "/api/health")
    if not health["worker_available"]:
        parser.error("Start the prediction worker first.")
    corpus = {row["id"]: row for row in json.loads((args.corpus / "corpus.json").read_text())}
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=5, check=True).stdout.strip().splitlines()[0]
    except (OSError, IndexError, subprocess.SubprocessError):
        gpu = "Unavailable to the benchmark process; inspect the worker host separately."
    report = {"gpu": gpu, "settings": {"num_models": 1, "num_recycle": 3,
              "msa_mode": "mmseqs2_uniref_env", "timeout": 1800},
              "limitations": "Single run per public sample. Includes MSA and initialization; not steady-state throughput. Device peak includes pre-existing desktop GPU allocations.",
              "results": []}
    for protein_id in args.ids:
        protein = corpus[protein_id]
        started = time.monotonic()
        memory = [gpu_memory()]
        job = request(args.url + "/api/jobs", {"sequence": protein["sequence"], "settings": report["settings"]})
        initial_cache_hit = job["cache_hit"]
        print(protein_id, len(protein["sequence"]), job["id"], job["status"], flush=True)
        last_status = None
        while job["status"] in {"queued", "running"}:
            if time.monotonic() - started > 1900:
                raise TimeoutError(f"Benchmark job did not finish: {job['id']}")
            if job["status"] != last_status:
                print(protein_id, job["status"], flush=True)
                last_status = job["status"]
            time.sleep(2)
            memory.append(gpu_memory())
            job = request(args.url + "/api/jobs/" + job["id"])
        manifest = job.get("manifest") or {}
        row = {"id": protein_id, "name": protein["name"], "organism": protein["organism"],
               "source_url": protein["source_url"], "length": len(protein["sequence"]),
               "job_id": job["id"], "status": job["status"], "error": job.get("error"),
               "initial_cache_hit": initial_cache_hit,
               "wall_seconds": round(time.monotonic() - started, 2), "plddt": manifest.get("plddt"),
               "device_baseline_mib": memory[0], "device_peak_used_mib": max((v for v in memory if v is not None), default=None)}
        if manifest.get("finished_at") and manifest.get("started_at"):
            row["prediction_seconds"] = (datetime.fromisoformat(manifest["finished_at"].replace("Z", "+00:00")) - datetime.fromisoformat(manifest["started_at"].replace("Z", "+00:00"))).total_seconds()
        if job["status"] == "succeeded":
            again = request(args.url + "/api/jobs", {"sequence": protein["sequence"], "settings": report["settings"]})
            row["cache_reused_same_job"] = again["id"] == job["id"] and again["cache_hit"]
            if not row["cache_reused_same_job"]:
                raise AssertionError("Completed result was not reused by cache.")
        report["results"].append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
