"""
Optional LOCAL code-embedding encoder (non-LLM) , the sanctioned semantic
precision upgrade for the SRP / OCP / LSP screens.

WHAT THIS IS. A small Hugging Face code encoder (default GraphCodeBERT, ~125M
params, ~500MB) that maps a chunk of code (a method / a branch body) to a fixed
vector, positioned so code with similar MEANING lands nearby even when the tokens
differ (`fetch_page` ≈ `download_html`). That "meaning, not tokens" jump is
exactly the precision ceiling of the three structural screens: god-vs-cohesive
class (SRP), open-vs-closed type set (OCP), real-contract-vs-plugin-placeholder
(LSP). Unlike an LLM it is small, runs on CPU or a 4GB GPU, needs no API, and is
DETERMINISTIC at inference (eval mode, no sampling) , so it preserves the
benchmark's reproducibility.

WHY IT IS OPTIONAL / GUARDED. The base pipeline's only third-party deps are
pandas/pyarrow/lizard/radon. `torch`+`transformers` are heavy and are NOT
required unless a run opts into an embedding-backed metric (all off by default in
config.py). So every import here is guarded: if the deps or the model are
missing, `available()` returns False and callers fall back to the deterministic
default. The pipeline NEVER breaks for lack of torch.

USAGE (callers must gate on the config flag AND on available()):
    import embeddings as E
    if C.SRP_C3_MODE == "embed" and E.available():
        vecs = E.embed([method_src_1, method_src_2, ...])   # (n, d) float32
        c3 = E.mean_pairwise_cosine(vecs)

Vectors are cached persistently (sqlite, keyed by sha1(model + text)) so a
re-run does not re-encode unchanged code. The cache is safe to delete.

TO ENABLE:  pip install torch transformers   (then set the config flag + re-run).
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

_LOCK = threading.Lock()
_STATE: dict = {"tried": False, "ok": False, "reason": None,
                "model": None, "tokenizer": None, "torch": None,
                "name": None, "cache": None}


# --------------------------------------------------------------------------
# Availability + lazy model load (all torch/transformers imports guarded)
# --------------------------------------------------------------------------
def available(model_name: str | None = None) -> bool:
    """True iff torch+transformers import AND the encoder loads. Memoized.
    Never raises , a missing dependency just returns False."""
    name = model_name or getattr(C, "EMBED_MODEL", "microsoft/graphcodebert-base")
    with _LOCK:
        if _STATE["tried"] and _STATE["name"] == name:
            return _STATE["ok"]
        _STATE.update(tried=True, ok=False, reason=None, name=name)
        try:
            import torch  # noqa: F401
            from transformers import AutoModel, AutoTokenizer
        except Exception as e:  # noqa: BLE001
            _STATE["reason"] = f"deps missing ({e}); pip install torch transformers"
            return False
        try:
            tok = AutoTokenizer.from_pretrained(name)
            model = AutoModel.from_pretrained(name)
            model.eval()
            _STATE.update(ok=True, model=model, tokenizer=tok, torch=torch)
        except Exception as e:  # noqa: BLE001
            _STATE["reason"] = f"model '{name}' failed to load: {e}"
            return False
        return True


def unavailable_reason() -> str | None:
    return _STATE.get("reason")


# --------------------------------------------------------------------------
# Persistent vector cache (stdlib sqlite; keyed by sha1(model + text))
# --------------------------------------------------------------------------
def _cache_conn() -> sqlite3.Connection | None:
    if _STATE["cache"] is not None:
        return _STATE["cache"]
    path = getattr(C, "EMBED_CACHE", None)
    if path is None:
        return None
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS vec (k TEXT PRIMARY KEY, v BLOB, dim INT)")
    _STATE["cache"] = conn
    return conn


def _key(name: str, text: str) -> str:
    return hashlib.sha1(f"{name}\x00{text}".encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------------
# Embedding
# --------------------------------------------------------------------------
def embed(texts: list[str], model_name: str | None = None,
          max_length: int = 512, batch_size: int = 16) -> np.ndarray | None:
    """Encode each text to an L2-normalized vector. Returns (n, d) float32, or
    None if the encoder is unavailable. Deterministic (eval mode, no grad).
    Empty/whitespace texts map to a zero vector. Cached persistently."""
    if not available(model_name):
        return None
    name = _STATE["name"]
    torch = _STATE["torch"]
    tok, model = _STATE["tokenizer"], _STATE["model"]
    conn = _cache_conn()

    out: list[np.ndarray | None] = [None] * len(texts)
    todo: list[int] = []
    for i, t in enumerate(texts):
        if not (t and t.strip()):
            out[i] = None  # filled with zeros once we know d
            continue
        if conn is not None:
            row = conn.execute("SELECT v FROM vec WHERE k=?", (_key(name, t),)).fetchone()
            if row is not None:
                out[i] = np.frombuffer(row[0], dtype=np.float32)
                continue
        todo.append(i)

    for b in range(0, len(todo), batch_size):
        idxs = todo[b:b + batch_size]
        batch = [texts[i] for i in idxs]
        enc = tok(batch, padding=True, truncation=True, max_length=max_length,
                  return_tensors="pt")
        with torch.no_grad():
            hidden = model(**enc).last_hidden_state          # (B, T, D)
        mask = enc["attention_mask"].unsqueeze(-1).float()   # (B, T, 1)
        summed = (hidden * mask).sum(1)
        counts = mask.sum(1).clamp(min=1e-9)
        pooled = (summed / counts).cpu().numpy().astype(np.float32)  # mean-pool
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        pooled = pooled / np.clip(norms, 1e-9, None)
        for j, i in enumerate(idxs):
            out[i] = pooled[j]
            if conn is not None:
                conn.execute("INSERT OR REPLACE INTO vec VALUES (?,?,?)",
                             (_key(name, texts[i]), pooled[j].tobytes(), pooled.shape[1]))
    if conn is not None:
        conn.commit()

    d = next((v.shape[0] for v in out if v is not None), 0)
    if d == 0:
        return None
    return np.stack([v if v is not None else np.zeros(d, np.float32) for v in out])


# --------------------------------------------------------------------------
# Similarity helpers (pure numpy , testable without torch)
# --------------------------------------------------------------------------
def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def mean_pairwise_cosine(vectors: np.ndarray | None) -> float | None:
    """Mean cosine over all unordered pairs , the embedding analogue of C3.
    None if fewer than 2 non-zero vectors."""
    if vectors is None:
        return None
    rows = [v for v in vectors if np.linalg.norm(v) > 1e-9]
    if len(rows) < 2:
        return None
    sims = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            sims.append(cosine(rows[i], rows[j]))
    return float(np.mean(sims)) if sims else None


# --------------------------------------------------------------------------
# Self-check (runs WITHOUT torch: exercises the pure numpy helpers + graceful
# degradation). Run: python embeddings.py
# --------------------------------------------------------------------------
if __name__ == "__main__":
    a = np.array([1.0, 0.0, 0.0], np.float32)
    b = np.array([1.0, 0.0, 0.0], np.float32)
    c = np.array([0.0, 1.0, 0.0], np.float32)
    assert abs(cosine(a, b) - 1.0) < 1e-6
    assert abs(cosine(a, c) - 0.0) < 1e-6
    assert mean_pairwise_cosine(np.stack([a, b])) == 1.0
    assert abs(mean_pairwise_cosine(np.stack([a, b, c])) - (1 + 0 + 0) / 3) < 1e-6
    assert mean_pairwise_cosine(np.stack([a])) is None
    assert mean_pairwise_cosine(None) is None
    # graceful degradation when torch is absent
    if not available():
        assert embed(["def f(): pass"]) is None
        print("embeddings.py self-check OK (encoder UNAVAILABLE , "
              f"{unavailable_reason()}); numpy helpers pass, callers will fall "
              "back to deterministic defaults.")
    else:
        v = embed(["def add(a, b):\n    return a + b",
                   "def total(x, y):\n    return x + y",
                   "def render_html(page):\n    return f'<h1>{page}</h1>'"])
        assert v is not None and v.shape[0] == 3
        related = cosine(v[0], v[1])
        unrelated = cosine(v[0], v[2])
        print(f"embeddings.py self-check OK (encoder={_STATE['name']}); "
              f"add~total={related:.3f} > add~render={unrelated:.3f}: "
              f"{related > unrelated}")
