"""Local web API; long predictions are owned exclusively by the queue worker."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import threading
import time
from typing import Literal
import zipfile

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .jobs import JobStore
from .search import ProteinSearch
from .sequence import parse_sequence

SAMPLE_SEQUENCE = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: str = Field(min_length=1, max_length=10000)
    settings: dict = Field(default_factory=dict)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: str = Field(min_length=1, max_length=10000)
    limit: int = Field(default=10, ge=1, le=100)


def create_app(root: str | Path | None = None, corpus_dir: str | Path | None = None) -> FastAPI:
    root = Path(root or os.getenv("PE_ROOT", "data")).resolve()
    corpus_dir = Path(corpus_dir or os.getenv("PE_CORPUS_DIR", str(root / "corpus"))).resolve()
    store = JobStore(root)
    search = ProteinSearch(corpus_dir)
    search_lock = threading.Lock()
    search_stamp = (corpus_dir / "index.json").stat().st_mtime_ns if (corpus_dir / "index.json").is_file() else None
    api = FastAPI(title="Protein Explorer", version="0.2.0")
    api.state.store, api.state.search = store, search

    def refresh_search():
        nonlocal search, search_stamp
        path = corpus_dir / "index.json"
        stamp = path.stat().st_mtime_ns if path.is_file() else None
        if stamp != search_stamp:
            with search_lock:
                search = ProteinSearch(corpus_dir)
                search_stamp = stamp
                api.state.search = search

    def engine() -> dict:
        try:
            return json.loads((root / "engine.json").read_text())
        except (OSError, ValueError):
            return {"available": False, "version": "unavailable", "fingerprint": "unavailable"}

    def job_or_404(job_id: str) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Prediction job not found.")
        return job

    def artifact(job: dict, name: str) -> Path:
        if job["status"] != "succeeded" or not job.get("output_dir"):
            raise HTTPException(409, "Prediction is not complete.")
        directory = Path(job["output_dir"]).resolve()
        path = (directory / name).resolve()
        if not directory.is_relative_to(root / "runs") or not path.is_relative_to(directory) or not path.is_file():
            raise HTTPException(404, "Prediction artifact is unavailable.")
        return path

    @api.get("/api/health")
    def health():
        refresh_search()
        try:
            heartbeat = json.loads((root / "worker-heartbeat.json").read_text())
            worker_available = time.time() - heartbeat["updated_at"] < 20
        except (OSError, ValueError, KeyError, TypeError):
            worker_available = False
        return {"status": "ok", "worker_available": worker_available, "engine": engine(),
                "corpus": search.info(), "sample": {"id": "1UBQ", "name": "Ubiquitin",
                "sequence": SAMPLE_SEQUENCE, "source_url": "https://www.rcsb.org/structure/1UBQ"}}

    @api.post("/api/jobs", status_code=202)
    def submit(body: JobRequest):
        identity = engine()
        if not identity.get("available"):
            raise HTTPException(503, "Start a configured prediction worker before submitting a job.")
        try:
            return store.submit(body.sequence, body.settings, identity["fingerprint"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.get("/api/jobs")
    def jobs(limit: int = Query(default=20, ge=1, le=100)):
        return store.list(limit)

    @api.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return job_or_404(job_id)

    @api.get("/api/jobs/{job_id}/structure")
    def job_structure(job_id: str):
        job = job_or_404(job_id)
        return Response(artifact(job, (job.get("manifest") or {}).get("pdb", "missing")).read_text(), media_type="text/plain")

    @api.get("/api/jobs/{job_id}/log")
    def job_log(job_id: str):
        job = job_or_404(job_id)
        directory = Path(job["output_dir"]) if job.get("output_dir") else root / "runs" / job_id / "attempt-1"
        path = directory / "colabfold.log"
        if not path.resolve().is_relative_to(root / "runs"):
            raise HTTPException(404, "Log not found.")
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 32000))
                return {"log": stream.read().decode(errors="replace")}
        except FileNotFoundError:
            return {"log": "No log yet."}

    @api.get("/api/jobs/{job_id}/download")
    def download(job_id: str, kind: Literal["pdb", "scores", "manifest", "bundle"] = "pdb"):
        job = job_or_404(job_id)
        manifest = job.get("manifest") or {}
        names = {"pdb": manifest.get("pdb", "missing"), "scores": manifest.get("scores", "missing"),
                 "manifest": "manifest.json"}
        if kind != "bundle":
            path = artifact(job, names[kind])
            return FileResponse(path, filename=path.name)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
            for name in dict.fromkeys(names.values()):
                target.write(artifact(job, name), arcname=name)
            directory = Path(job["output_dir"])
            for name in ("environment.json", "config.json", "checksums.json", "validation.json", "engine.json", "query.fasta"):
                if (directory / name).is_file():
                    target.write(artifact(job, name), arcname=name)
        return Response(archive.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{job_id}-results.zip"'})

    @api.post("/api/search")
    def search_proteins(body: SearchRequest):
        refresh_search()
        try:
            sequence = parse_sequence(body.sequence)
            with search_lock:
                results = search.search(sequence, body.limit)
            return {"results": results, "model": search.info()["model"], "count": search.info()["count"]}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @api.get("/api/proteins/{protein_id}/structure")
    def reference_structure(protein_id: str):
        refresh_search()
        try:
            return Response(search.structure(protein_id), media_type="text/plain")
        except KeyError as exc:
            raise HTTPException(404, "Reference protein not found.") from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(502, str(exc)) from exc

    static = Path(__file__).parent / "static"
    api.mount("/static", StaticFiles(directory=static), name="static")

    @api.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return api


app = create_app()
