FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PE_ROOT=/data \
    PE_CORPUS_DIR=/data/corpus \
    HF_HOME=/models/huggingface

WORKDIR /app

ARG TORCH_CPU_VERSION=2.7.1+cpu

COPY pyproject.toml ./

# Resolve all optional dependencies from project metadata. Pin the CPU wheel first so
# the search extra cannot replace it with a CUDA-enabled PyPI build.
RUN python -m pip install --no-cache-dir \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      "torch==${TORCH_CPU_VERSION}"

RUN python - <<'PY'
import re
import subprocess
import sys
import tomllib

with open("pyproject.toml", "rb") as stream:
    metadata = tomllib.load(stream)
requirements = [
    requirement
    for extra in ("web", "search")
    for requirement in metadata["project"]["optional-dependencies"].get(extra, [])
    if re.match(r"^[A-Za-z0-9_.-]+", requirement).group(0).lower() != "torch"
]
subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", *requirements])
PY

COPY README.md ./
COPY src ./src

COPY scripts ./scripts

RUN python -m pip install --no-cache-dir --no-deps .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]

CMD ["uvicorn", "protein_explorer.api:app", "--host", "0.0.0.0", "--port", "8000"]
