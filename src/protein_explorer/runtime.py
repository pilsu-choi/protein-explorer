"""Identify the configured prediction environment without importing its packages."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

_IDENTITIES: dict[tuple, dict] = {}


def engine_identity(executable: str, data_dir: str | None) -> dict:
    resolved = shutil.which(executable)
    if not resolved:
        return {"available": False, "version": "unavailable", "fingerprint": "unavailable",
                "error": "ColabFold executable is not installed."}
    python = Path(resolved).parent / "python"
    weight_paths = sorted((Path(data_dir) / "params").glob("params_model_*_ptm.npz")) if data_dir else []
    metadata_paths = list(Path(resolved).parent.parent.glob("lib/python*/site-packages/*.dist-info/METADATA"))
    environment = {key: os.getenv(key, "") for key in (
        "JAX_PLATFORMS", "TF_FORCE_UNIFIED_MEMORY", "XLA_PYTHON_CLIENT_MEM_FRACTION", "XLA_PYTHON_CLIENT_PREALLOCATE")}
    signature = (tuple(sorted(environment.items())), tuple(
        (str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns)
        for path in [Path(resolved), *weight_paths, *sorted(metadata_paths)]))
    if signature in _IDENTITIES:
        return dict(_IDENTITIES[signature])
    probe = (
        "import importlib.metadata as m,json,sys; "
        "names=['colabfold','alphafold-colabfold','jax','jaxlib','numpy','dm-haiku']; "
        "print(json.dumps({'python':sys.version,'packages':{n:m.version(n) for n in names}}))"
    )
    try:
        result = subprocess.run([str(python), "-c", probe], capture_output=True, text=True,
                                timeout=30, check=True)
        metadata = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"available": False, "version": "unavailable", "fingerprint": "unavailable",
                "error": f"Cannot inspect the configured ColabFold environment: {exc}"}
    weights = {}
    if data_dir:
        for path in weight_paths:
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            weights[path.name] = digest.hexdigest()
    metadata.update({"weights": weights or {"source": "alphafold_params_2021-07-14", "download_pending": True},
                     "runner_schema": 1,
                     "environment": environment})
    fingerprint = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    identity = {**metadata, "available": True, "version": metadata["packages"]["colabfold"],
                "fingerprint": fingerprint}
    _IDENTITIES.clear()
    _IDENTITIES[signature] = identity
    return dict(identity)
