#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="${1:-${PROTEIN_EXPLORER_RUNTIME:-$repo_root/.runtime}}"
runtime_dir="$(cd -- "$runtime_dir" && pwd)"
output_dir="${2:-$runtime_dir/runs/1ubq-$(date -u +%Y%m%dT%H%M%SZ)}"
prediction_python="$runtime_dir/colabfold/bin/python"

export XDG_CACHE_HOME="$runtime_dir/cache"
export XDG_DATA_HOME="$runtime_dir/data"
export XLA_PYTHON_CLIENT_PREALLOCATE=false
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.5
export TF_FORCE_UNIFIED_MEMORY=0
# AlphaFold also explicitly targets CPU for some operations; CUDA remains first.
export JAX_PLATFORMS=cuda,cpu
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
export PYTHONPATH="$repo_root/src"
# WSL crash collection can retain multi-GB dumps after a native CUDA failure.
ulimit -c 0

"$prediction_python" -c 'import jax; print("JAX:", jax.__version__, "devices:", jax.devices()); assert jax.default_backend() == "gpu", "CUDA GPU is required"'
exec python3 -m protein_explorer.cli predict --file "$repo_root/examples/1ubq.fasta" \
  --colabfold-batch "$runtime_dir/colabfold/bin/colabfold_batch" \
  --output-dir "$output_dir" --data-dir "$runtime_dir/weights" \
  --model-type alphafold2_ptm --msa-mode mmseqs2_uniref_env \
  --num-models 1 --num-recycle 3 --timeout 3600
