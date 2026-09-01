"""LangChain + Chroma physical path for RelOp knn. Dummy overlay only.

The SLM never writes Chroma where-filters. RelOp binds (strategy, column, entity)
become metadata equality. Live Superstore is never copied into Chroma.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from revolverelate.catalog import spec_dir
from revolverelate.vector.overlay import OVERLAY

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")

_CLIENTS: dict[str, object] = {}
_STORES: dict[tuple[str, str], object] = {}


def load_rag_spec() -> dict:
    return json.loads((spec_dir() / "vector-rag.json").read_text(encoding="utf-8"))


def chroma_opted_in() -> bool:
    return os.environ.get("REVOLVERELATE_CHROMA", "").strip().casefold() in {"1", "true", "yes"}


def chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401
        import langchain_core  # noqa: F401
        import langchain_chroma  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache(maxsize=2)
def embedding_model():
    """LangChain Embeddings: Chroma ONNX MiniLM, or HuggingFace if REVOLVERELATE_EMBED=hf."""
    spec = load_rag_spec().get("chroma", {}).get("embedding") or {}
    model_id = str(spec.get("id") or "all-MiniLM-L6-v2")
    if os.environ.get("REVOLVERELATE_EMBED", "").casefold() in {"hf", "huggingface"}:
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name=model_id), model_id, "langchain-huggingface"
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    from langchain_core.embeddings import Embeddings

    class _ChromaMiniLM(Embeddings):
        def __init__(self):
            self._fn = DefaultEmbeddingFunction()

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[float(x) for x in v] for v in self._fn(list(texts))]

        def embed_query(self, text: str) -> list[float]:
            return [float(x) for x in self._fn([text])[0]]

    return _ChromaMiniLM(), model_id, "langchain+chroma-onnx"


def embed_sentences(texts: list[str]) -> list[list[float]]:
    model, _, _ = embedding_model()
    return model.embed_documents(list(texts))


def chroma_dir(workdir: str | Path) -> Path:
    path = Path(workdir) / ".revolverelate" / "chroma"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _where(*, strategy: str | None, column: str | None, entity: str | None) -> dict | None:
    clauses = []
    if strategy:
        clauses.append({"strategy": {"$eq": str(strategy)}})
    if column:
        clauses.append({"column": {"$eq": str(column)}})
    if entity:
        clauses.append({"entity": {"$eq": str(entity)}})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _client(workdir: str | Path):
    persist = str(chroma_dir(workdir))
    cached = _CLIENTS.get(persist)
    if cached is not None:
        return cached
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=persist,
        settings=Settings(anonymized_telemetry=False, allow_reset=True),
    )
    _CLIENTS[persist] = client
    return client


def _store(workdir: str | Path, *, reset: bool = False):
    from langchain_chroma import Chroma

    spec = load_rag_spec().get("chroma") or {}
    name = str(spec.get("collection") or "rr_overlay")
    persist = str(chroma_dir(workdir))
    key = (persist, name)
    client = _client(workdir)
    if reset:
        try:
            client.delete_collection(name)
        except Exception:
            pass
        _STORES.pop(key, None)
    cached = _STORES.get(key)
    if cached is not None:
        return cached
    emb, _, _ = embedding_model()
    store = Chroma(client=client, collection_name=name, embedding_function=emb)
    _STORES[key] = store
    return store


def sync_chroma(sandbox, workdir: str | Path, *, strategies: tuple[str, ...] | None = None) -> dict:
    if not chroma_available():
        return {"ok": False, "reason": "chromadb/langchain extras not installed", "count": 0}
    spec = load_rag_spec().get("chroma") or {}
    want = tuple(strategies or spec.get("strategies") or ("semantic", "causal"))
    try:
        rows = sandbox._conn.execute(
            f'''SELECT ChunkId, Entity, SourcePk, Column, Strategy, Level, Text, Cue, Role
                FROM "{OVERLAY}" WHERE Strategy IN ({",".join("?" * len(want))}) AND Text IS NOT NULL''',
            list(want),
        ).fetchall()
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "count": 0}
    from langchain_core.documents import Document

    store = _store(workdir, reset=True)
    docs, ids = [], []
    for chunk_id, entity, source_pk, column, strategy, level, text, cue, role in rows:
        body = (text or "").strip()
        if not body:
            continue
        ids.append(str(chunk_id))
        docs.append(
            Document(
                page_content=body,
                metadata={
                    "chunk_id": int(chunk_id),
                    "entity": str(entity),
                    "source_pk": str(source_pk),
                    "column": str(column),
                    "strategy": str(strategy),
                    "level": str(level or ""),
                    "cue": str(cue or ""),
                    "role": str(role or ""),
                },
            )
        )
    if docs:
        store.add_documents(docs, ids=ids)
    model, model_id, via = embedding_model()
    return {
        "ok": True,
        "count": len(docs),
        "strategies": list(want),
        "collection": (load_rag_spec().get("chroma") or {}).get("collection"),
        "model": model_id,
        "via": via,
        "path": str(chroma_dir(workdir)),
    }


def query_chroma(
    workdir: str | Path,
    query: str,
    *,
    strategy: str | None = "semantic",
    column: str | None = None,
    entity: str | None = None,
    n: int = 5,
) -> list[dict]:
    if not chroma_available():
        raise RuntimeError("chromadb/langchain extras not installed")
    store = _store(workdir)
    where = _where(strategy=strategy, column=column, entity=entity)
    k = max(int(n), 1)
    raw = store.similarity_search_with_score(query, k=k, filter=where)
    hits = []
    for doc, dist in raw:
        meta = doc.metadata or {}
        distance = float(dist)
        hits.append(
            {
                "Text": doc.page_content,
                "SourcePk": meta.get("source_pk"),
                "Entity": meta.get("entity"),
                "Column": meta.get("column"),
                "Strategy": meta.get("strategy"),
                "Role": meta.get("role") or "",
                "Cue": meta.get("cue") or "",
                "score": round(max(0.0, 1.0 - distance), 6),
                "dist": round(distance, 6),
            }
        )
    hits.sort(key=lambda r: r["dist"])
    return hits


def chroma_status(workdir: str | Path) -> dict:
    if not chroma_available():
        return {"ok": False, "available": False, "reason": "install extras: pip install -e python[chroma]"}
    persist = chroma_dir(workdir)
    spec = load_rag_spec().get("chroma") or {}
    name = str(spec.get("collection") or "rr_overlay")
    count = 0
    try:
        col = _client(workdir).get_collection(name)
        count = col.count()
    except Exception:
        count = 0
    _, model_id, via = embedding_model()
    return {
        "ok": count > 0,
        "available": True,
        "count": count,
        "collection": name,
        "model": model_id,
        "via": via,
        "path": str(persist),
    }


def rag(
    rr,
    query: str,
    *,
    strategy: str = "semantic",
    column: str = "ProductName",
    n: int = 5,
    live: bool = True,
) -> dict:
    """RelOp retrieve on dummy overlay, then the same RelOp on live text chunks. Never SQL from the model."""
    composite = "rag_causal_knn" if strategy == "causal" else "rag_semantic_knn"
    plan = rr.analytics.run_chain(
        [
            {"op": "overlay", "column": column},
            {"op": f"chunk_{strategy}", "column": column},
            {"op": "knn", "query": query, "n": n, "column": column},
        ],
        composite=None,
        plan_id=f"rag-{strategy}",
    )
    chroma_hits: list[dict] = []
    status = {"available": False, "optedIn": False}
    if chroma_opted_in():
        status = chroma_status(rr.workdir)
    if status.get("available"):
        if not status.get("count"):
            sync_chroma(rr.sandbox, rr.workdir)
            status = chroma_status(rr.workdir)
        try:
            chroma_hits = query_chroma(rr.workdir, query, strategy=strategy, column=column, n=n)
        except Exception as exc:
            status = {**status, "queryError": str(exc)}
    live_out = rr.replay_live(plan_id=plan.get("id")) if live else {"ran": False}
    return {
        "query": query,
        "strategy": strategy,
        "column": column,
        "composite": composite,
        "relop": {
            "status": plan.get("status"),
            "sql": plan.get("sql"),
            "params": plan.get("params"),
            "columns": plan.get("columns"),
            "rows": plan.get("rows"),
            "rowCount": plan.get("rowCount"),
            "id": plan.get("id"),
            "target": "sandbox",
        },
        "live": live_out,
        "chroma": chroma_hits,
        "backend": status,
        "sandboxOnly": False,
        "overlayPromoted": bool(live_out.get("ran")),
        "hint": "Dummy staged the RelOp. Live replay chunks non-PII live text into the same overlay fields. Chroma MiniLM stays a local physical path.",
    }
