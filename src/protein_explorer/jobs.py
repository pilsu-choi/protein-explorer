"""SQLite-backed prediction queue and validated result cache."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .predict import PredictionError, _validate_outputs
from .sequence import parse_sequence

_DEFAULTS = {"num_models": 1, "num_recycle": 3, "msa_mode": "mmseqs2_uniref_env",
             "model_type": "alphafold2_ptm", "timeout": 7200}
_INFERENCE_KEYS = ("num_models", "num_recycle", "msa_mode", "model_type")


class JobStore:
    """Persist queued, running, and succeeded predictions under root."""

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(exist_ok=True)
        self.db_path = self.root / "jobs.sqlite3"
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, sequence TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
                settings TEXT NOT NULL, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, error TEXT, output_dir TEXT,
                manifest TEXT, cache_hit INTEGER NOT NULL DEFAULT 0,
                engine_version TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_cache_status ON jobs(cache_key, status, created_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level="DEFERRED")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _settings(settings: dict) -> dict:
        if not isinstance(settings, dict):
            raise TypeError("settings must be a dictionary")
        unknown = set(settings) - set(_DEFAULTS)
        if unknown:
            raise ValueError(f"Unknown prediction setting(s): {', '.join(sorted(unknown))}")
        effective = {**_DEFAULTS, **settings}
        for key in ("num_models", "num_recycle", "timeout"):
            if isinstance(effective[key], bool) or not isinstance(effective[key], int):
                raise ValueError(f"{key} must be an integer")
        if not isinstance(effective["model_type"], str) or not isinstance(effective["msa_mode"], str):
            raise ValueError("model_type and msa_mode must be strings")
        if not 1 <= effective["timeout"] <= 86400:
            raise ValueError("timeout must be between 1 and 86400 seconds")
        if not 0 <= effective["num_recycle"] <= 20:
            raise ValueError("num_recycle must be between 0 and 20")
        if not 1 <= effective["num_models"] <= 5:
            raise ValueError("num_models must be between 1 and 5")
        if effective["model_type"] != "alphafold2_ptm":
            raise ValueError("model_type currently supports alphafold2_ptm only")
        if effective["msa_mode"] not in {"mmseqs2_uniref_env", "single_sequence"}:
            raise ValueError("msa_mode must be mmseqs2_uniref_env or single_sequence")
        return effective

    @staticmethod
    def _cache_key(sequence: str, settings: dict, engine_version: str) -> str:
        # Timeout does not alter inference. engine_version should include package/model
        # fingerprints when available.
        identity = {"sequence": sequence, "settings": {k: settings[k] for k in _INFERENCE_KEYS},
                    "engine_version": engine_version}
        return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @classmethod
    def _cached_outputs_valid(cls, row: sqlite3.Row, sequence: str) -> bool:
        try:
            pdb, scores, _summary = _validate_outputs(Path(row["output_dir"]), sequence)
            manifest = json.loads(row["manifest"]) if row["manifest"] else {}
            if not isinstance(manifest, dict):
                return False
            expected = manifest.get("artifacts_sha256", {})
            if not isinstance(expected, dict):
                return False
            return (manifest.get("status") == "succeeded"
                    and expected.get(pdb.name) == cls._sha256(pdb)
                    and expected.get(scores.name) == cls._sha256(scores))
        except (OSError, ValueError, TypeError, PredictionError):
            return False

    @staticmethod
    def _decode(row: sqlite3.Row, cache_hit: bool | None = None) -> dict[str, Any]:
        item = dict(row)
        item["settings"] = json.loads(item["settings"])
        item["manifest"] = json.loads(item["manifest"]) if item["manifest"] else None
        item["cache_hit"] = bool(item["cache_hit"]) if cache_hit is None else cache_hit
        item.pop("cache_key", None)
        return item

    def submit(self, sequence: str, settings: dict, engine_version: str = "unknown") -> dict:
        sequence = parse_sequence(sequence)
        effective = self._settings(settings)
        if not isinstance(engine_version, str) or not engine_version:
            raise ValueError("engine_version must be a non-empty string")
        cache_key = self._cache_key(sequence, effective, engine_version)
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT * FROM jobs WHERE cache_key=? AND status IN ('queued','running') ORDER BY created_at LIMIT 1",
                                (cache_key,)).fetchone()
            if active:
                return self._decode(active, cache_hit=True)
            cached = db.execute("SELECT * FROM jobs WHERE cache_key=? AND status='succeeded' ORDER BY finished_at DESC",
                                (cache_key,)).fetchall()
            for row in cached:
                if not self._cached_outputs_valid(row, sequence):
                    continue
                return self._decode(row, cache_hit=True)
            job_id = str(uuid.uuid4())
            db.execute("INSERT INTO jobs(id,cache_key,sequence,status,settings,created_at,cache_hit,engine_version) "
                       "VALUES(?,?,?,'queued',?,?,0,?)",
                       (job_id, cache_key, sequence, json.dumps(effective, sort_keys=True), now, engine_version))
            return self._decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def get(self, job_id: str) -> dict | None:
        try:
            normalized = str(uuid.UUID(str(job_id)))
        except (ValueError, TypeError, AttributeError):
            return None
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (normalized,)).fetchone()
        return self._decode(row) if row else None

    def list(self, limit: int = 20) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?",
                              (min(limit, 500),)).fetchall()
        return [self._decode(row) for row in rows]

    def claim(self, job_id: str | None = None) -> dict | None:
        normalized = None
        if job_id is not None:
            try:
                normalized = str(uuid.UUID(str(job_id)))
            except (ValueError, TypeError, AttributeError):
                return None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if normalized is None:
                row = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            else:
                row = db.execute("SELECT id FROM jobs WHERE id=? AND status='queued'", (normalized,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running',started_at=? WHERE id=? AND status='queued'",
                       (self._now(), row["id"]))
            claimed = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            return self._decode(claimed)

    def complete(self, job_id: str, output_dir: str, manifest: dict,
                 engine_version: str | None = None) -> None:
        normalized = str(uuid.UUID(str(job_id)))
        row = self.get(normalized)
        if row is None or row["status"] != "running":
            return
        if not isinstance(manifest, dict) or manifest.get("status") != "succeeded":
            raise ValueError("A completed job requires a succeeded prediction manifest.")
        pdb, scores, _summary = _validate_outputs(Path(output_dir), row["sequence"])
        if manifest.get("pdb") != pdb.name or manifest.get("scores") != scores.name:
            raise ValueError("Prediction manifest does not identify the validated ranked outputs.")
        manifest = dict(manifest)
        manifest["artifacts_sha256"] = {
            pdb.name: self._sha256(pdb),
            scores.name: self._sha256(scores),
        }
        if engine_version is not None and (not isinstance(engine_version, str) or not engine_version):
            raise ValueError("engine_version must be a non-empty string")
        if engine_version is not None:
            manifest["engine_fingerprint"] = engine_version
        manifest_path = Path(output_dir) / "manifest.json"
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, manifest_path)
        if engine_version is None:
            self._finish(normalized, "succeeded", output_dir=output_dir, manifest=manifest)
            return
        cache_key = self._cache_key(row["sequence"], row["settings"], engine_version)
        with self._connect() as db:
            db.execute("UPDATE jobs SET status='succeeded',finished_at=?,output_dir=?,manifest=?,"
                       "engine_version=?,cache_key=? WHERE id=? AND status='running'",
                       (self._now(), output_dir, json.dumps(manifest, sort_keys=True),
                        engine_version, cache_key, normalized))

    def fail(self, job_id: str, message: str) -> None:
        self._finish(job_id, "failed", error=str(message))

    def _finish(self, job_id: str, status: str, *, output_dir: str | None = None,
                manifest: dict | None = None, error: str | None = None) -> None:
        try:
            normalized = str(uuid.UUID(str(job_id)))
        except (ValueError, TypeError, AttributeError):
            return
        with self._connect() as db:
            db.execute("UPDATE jobs SET status=?,finished_at=?,output_dir=?,manifest=?,error=? "
                       "WHERE id=? AND status='running'",
                       (status, self._now(), output_dir,
                        json.dumps(manifest, sort_keys=True) if manifest is not None else None,
                        error, normalized))

    def recover(self) -> int:
        """Fail rows left running by a prior process; call while holding worker lock."""
        with self._connect() as db:
            cursor = db.execute("UPDATE jobs SET status='failed',finished_at=?,"
                                "error='Worker stopped before recording a result.' WHERE status='running'",
                                (self._now(),))
            return cursor.rowcount
