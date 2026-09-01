"""Deterministic bag-of-tokens embeddings. No live model required for the dummy overlay."""

from __future__ import annotations

import hashlib
import json
import math
import re

_TOKEN = re.compile(r"[a-z0-9]+", re.I)
DIM = 16
MODEL = "hash-16"


def tokenize(text: str) -> list[str]:
    return [m.group(0).casefold() for m in _TOKEN.finditer(text or "")]


def hash_embed(text: str, *, dim: int = DIM) -> list[float]:
    vec = [0.0] * dim
    for tok in tokenize(text):
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:2], "little") % dim
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def fingerprint(text: str) -> int:
    digest = hashlib.blake2b((text or "").encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little") % 2_147_483_647


def cosine(left: list[float], right: list[float]) -> float:
    n = min(len(left), len(right))
    if n == 0:
        return 0.0
    return sum(left[i] * right[i] for i in range(n))


def pack_vec(vec: list[float]) -> str:
    return json.dumps([round(v, 6) for v in vec])


def rank_by_cosine(query: str, texts: list[str], *, dim: int = DIM) -> list[tuple[float, str]]:
    """Python-side cosine rank. RelOp knn still uses Hash; this is the swap-ready scorer."""
    qv = hash_embed(query, dim=dim)
    ranked = [(cosine(qv, hash_embed(t, dim=dim)), t) for t in texts]
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked


def embed_row(text: str, *, dim: int = DIM) -> dict:
    vec = hash_embed(text, dim=dim)
    return {
        "vec": vec,
        "packed": pack_vec(vec),
        "norm": 1.0,
        "hash": fingerprint(text),
        "model": MODEL,
        "dim": dim,
    }
