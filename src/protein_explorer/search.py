"""ESM-2 sequence embeddings and a small on-disk cosine search index."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

MODEL_NAME = "facebook/esm2_t6_8M_UR50D"
MODEL_REVISION = "c731040fcd8d73dceaa04b0a8e6329b345b0f5df"
POOLING = "mean_residue_tokens_excluding_padding_and_special_tokens"
NORMALIZATION = "l2"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def sequence_checksum(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


class ESMEncoder:
    """Lazy CPU ESM-2 encoder. Heavy torch/transformers imports happen on use."""

    def __init__(self, model_name: str = MODEL_NAME, revision: str = MODEL_REVISION):
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("Search requires torch and transformers; install the search dependencies first.") from exc
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision)
        self.model = AutoModel.from_pretrained(model_name, revision=revision, add_pooling_layer=False).to("cpu").eval()
        self.dimension = int(self.model.config.hidden_size)

    def encode(self, sequences: list[str], batch_size: int = 32) -> np.ndarray:
        vectors: list[np.ndarray] = []
        with self.torch.inference_mode():
            for start in range(0, len(sequences), batch_size):
                batch = sequences[start : start + batch_size]
                tokens = self.tokenizer(batch, return_tensors="pt", padding=True,
                                        truncation=False, return_special_tokens_mask=True)
                special = tokens.pop("special_tokens_mask").bool()
                attention = tokens["attention_mask"].bool()
                output = self.model(**tokens).last_hidden_state
                residue_mask = attention & ~special
                weights = residue_mask.unsqueeze(-1).to(output.dtype)
                pooled = (output * weights).sum(dim=1) / weights.sum(dim=1).clamp(min=1)
                normalized = self.torch.nn.functional.normalize(pooled, p=2, dim=1)
                vectors.append(normalized.cpu().numpy().astype(np.float32, copy=False))
        if not vectors:
            return np.empty((0, self.dimension), dtype=np.float32)
        return np.concatenate(vectors, axis=0)


class ProteinSearch:
    """Load a validated corpus index, search it, and lazily fetch structures."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.corpus_path = self.root / "corpus.json"
        self.embeddings_path = self.root / "embeddings.npy"
        self.manifest_path = self.root / "index.json"
        self._encoder: ESMEncoder | None = None
        self._corpus: list[dict[str, Any]] = []
        self._vectors: np.ndarray | None = None
        self._manifest: dict[str, Any] = {}
        self._error: str | None = None
        self._load_index()

    @property
    def ready(self) -> bool:
        return self._vectors is not None

    def _load_index(self) -> None:
        try:
            records = json.loads(self.corpus_path.read_text(encoding="utf-8"))
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            vectors = np.load(self.embeddings_path, allow_pickle=False)
            if not isinstance(records, list) or not records:
                raise ValueError("corpus must be a nonempty JSON list")
            if manifest.get("model") != MODEL_NAME or manifest.get("revision") != MODEL_REVISION:
                raise ValueError("model or revision mismatch")
            if manifest.get("pooling") != POOLING or manifest.get("normalization") != NORMALIZATION:
                raise ValueError("embedding configuration mismatch")
            if vectors.ndim != 2 or vectors.shape[0] != len(records):
                raise ValueError("embedding dimensions do not match corpus")
            if vectors.shape[1] != manifest.get("dimension"):
                raise ValueError("embedding dimension does not match manifest")
            if manifest.get("count") != len(records):
                raise ValueError("corpus count does not match manifest")
            checksums = [sequence_checksum(item["sequence"]) for item in records]
            if manifest.get("sequence_checksums") != checksums:
                raise ValueError("corpus sequence fingerprints do not match index")
            fingerprint = _corpus_fingerprint(records)
            if manifest.get("corpus_fingerprint") != fingerprint:
                raise ValueError("corpus metadata fingerprint does not match index")
            if not np.isfinite(vectors).all():
                raise ValueError("embedding matrix contains non-finite values")
            norms = np.linalg.norm(vectors, axis=1)
            if not np.allclose(norms, 1.0, atol=1e-4):
                raise ValueError("stored embeddings are not L2-normalized")
            self._corpus, self._vectors, self._manifest = records, vectors.astype(np.float32), manifest
        except FileNotFoundError:
            self._error = "index files not prepared"
        except OSError:
            self._error = "could not read index files"
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._error = str(exc)

    def _get_encoder(self) -> ESMEncoder:
        if self._encoder is None:
            self._encoder = ESMEncoder()
        return self._encoder

    def search(self, sequence: str, limit: int = 10) -> list[dict[str, Any]]:
        if not self.ready:
            raise RuntimeError(f"Protein search index is unavailable: {self._error or 'index not prepared'}")
        if not isinstance(sequence, str) or not sequence.strip():
            raise ValueError("sequence must be a nonempty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        query = self._get_encoder().encode([sequence])[0]
        if query.shape[0] != self._vectors.shape[1]:
            raise RuntimeError("query embedding dimension differs from the corpus index")
        scores = self._vectors @ query
        order = np.argsort(-scores, kind="stable")[:limit]
        results = []
        for index in order:
            row = dict(self._corpus[int(index)])
            row["length"] = len(row["sequence"])
            row["score"] = float(scores[index])
            results.append(row)
        return results

    def get(self, protein_id: str) -> dict[str, Any] | None:
        if not _SAFE_ID.fullmatch(protein_id or ""):
            return None
        for item in self._corpus:
            if item.get("id") == protein_id:
                result = dict(item)
                result["length"] = len(result["sequence"])
                return result
        return None

    def structure(self, protein_id: str) -> str:
        item = self.get(protein_id)
        if item is None:
            raise KeyError(protein_id)
        structure_url = item.get("structure_url")
        chain_id = item.get("chain_id")
        if not structure_url or not chain_id:
            raise ValueError(f"No structure source or chain is recorded for {protein_id}")
        cache = self.root / "structures"
        cache.mkdir(parents=True, exist_ok=True)
        cached = cache / f"{protein_id}.pdb"
        if cached.is_file():
            return cached.read_text(encoding="utf-8")
        request = Request(structure_url, headers={"User-Agent": "Protein-Explorer/0.1 (public corpus)"})
        try:
            with urlopen(request, timeout=30) as response:
                pdb_text = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, UnicodeError) as exc:
            raise RuntimeError(f"Could not fetch structure for {protein_id}: {exc}") from exc
        selected = _select_pdb_chain(pdb_text, str(chain_id))
        if not selected:
            raise RuntimeError(f"Fetched structure for {protein_id} has no atoms for chain {chain_id!r}")
        cached.write_text(selected, encoding="utf-8")
        return selected

    def info(self) -> dict[str, Any]:
        return {"ready": self.ready, "count": len(self._corpus), "model": MODEL_NAME,
                "revision": MODEL_REVISION, "pooling": POOLING, "normalization": NORMALIZATION,
                "dimension": self._manifest.get("dimension"), "error": self._error}


def embed_sequences(sequences: list[str], batch_size: int = 32) -> np.ndarray:
    """Encode a batch using the same settings as query-time search."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    return ESMEncoder().encode(sequences, batch_size=batch_size)


def make_index_manifest(records: list[dict[str, Any]], vectors: np.ndarray) -> dict[str, Any]:
    return {"model": MODEL_NAME, "revision": MODEL_REVISION, "pooling": POOLING,
            "normalization": NORMALIZATION, "dimension": int(vectors.shape[1]),
            "count": len(records), "sequence_checksums": [sequence_checksum(item["sequence"]) for item in records],
            "corpus_fingerprint": _corpus_fingerprint(records)}


def _corpus_fingerprint(records: list[dict[str, Any]]) -> str:
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _select_pdb_chain(text: str, chain_id: str) -> str:
    lines = []
    atoms_found = False
    has_models = False
    in_first_model = True
    for line in text.splitlines():
        if line.startswith("MODEL "):
            if has_models:
                break
            has_models = True
            in_first_model = True
            continue
        if line.startswith("ENDMDL") and has_models:
            break
        if line.startswith(("ATOM  ", "HETATM", "ANISOU", "TER   ")) and len(line) > 21:
            if in_first_model and line[21].strip() == chain_id.strip():
                lines.append(line)
                if line.startswith(("ATOM  ", "HETATM")):
                    atoms_found = True
        elif line.startswith(("HEADER", "TITLE ", "COMPND", "SOURCE", "KEYWDS", "EXPDTA", "REMARK")):
            lines.append(line)
    if not atoms_found:
        return ""
    return "\n".join(lines + ["TER", "END"]) + "\n"
