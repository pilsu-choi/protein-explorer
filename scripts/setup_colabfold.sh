#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_dir="${1:-${PROTEIN_EXPLORER_RUNTIME:-$repo_root/.runtime}}"

if [[ "$(uname -sm)" != 'Linux x86_64' ]]; then
  echo 'This lockfile targets Linux x86_64. ARM/GB10 needs a separately validated environment.' >&2
  exit 2
fi
command -v uv >/dev/null || { echo 'Install uv before running this script.' >&2; exit 2; }
mkdir -p "$runtime_dir"
runtime_dir="$(cd -- "$runtime_dir" && pwd)"
export UV_CACHE_DIR="$runtime_dir/uv-cache"
export UV_PYTHON_INSTALL_DIR="$runtime_dir/python"
export UV_PYTHON_BIN_DIR="$runtime_dir/bin"

uv python install 3.12.13
if [[ ! -x "$runtime_dir/colabfold/bin/python" ]]; then
  uv venv --python 3.12.13 "$runtime_dir/colabfold"
fi
"$runtime_dir/colabfold/bin/python" -c 'import sys; assert sys.version_info[:3] == (3, 12, 13), "Choose a fresh runtime directory; this environment uses a different Python version."'
uv pip sync --python "$runtime_dir/colabfold/bin/python" "$repo_root/requirements/colabfold-linux-x86_64.lock"
printf 'ColabFold executable: %s\n' "$runtime_dir/colabfold/bin/colabfold_batch"
echo 'Model weights are downloaded on first prediction. This script does not run inference.'
