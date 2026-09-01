"""LangChain + Chroma MiniLM retrieve for semantic and causal RelOp."""

from __future__ import annotations

import pytest

chromadb = pytest.importorskip("chromadb")
pytest.importorskip("langchain_core")
pytest.importorskip("langchain_chroma")

from revolverelate.mcp.server import dispatch
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore
from revolverelate.vector.chroma_store import chroma_status, query_chroma, rag, sync_chroma
from revolverelate.vector.overlay import OVERLAY


def test_chroma_sync_and_semantic_knn(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=6)
    status = chroma_status(tmp_path)
    if not status.get("count"):
        synced = sync_chroma(rr.sandbox, tmp_path)
        assert synced["ok"] is True
        assert synced["count"] > 0
        status = chroma_status(tmp_path)
    assert status["available"] is True
    assert status["count"] > 0
    assert {e.name for e in rr.schema.all_entities()} == {"Customer", "Product", "Orders", "OrderLine"}
    hits = query_chroma(tmp_path, "bookcase binders shelves", strategy="semantic", column="ProductName", n=5)
    assert hits
    assert all(row["Strategy"] == "semantic" for row in hits)
    assert all(row["Column"] == "ProductName" for row in hits)
    top = " ".join(row["Text"].casefold() for row in hits[:3])
    assert "bookcase" in top or "binder" in top or "shelf" in top
    rr.close()


def test_chroma_causal_filters_and_roles(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    sync_chroma(rr.sandbox, tmp_path)
    hits = query_chroma(tmp_path, "sales fell because discounting", strategy="causal", column="ProductName", n=8)
    assert hits
    assert all(row["Strategy"] == "causal" for row in hits)
    top = hits[0]
    assert top.get("Cue") in {"because", "therefore"} or top.get("Role") in {"cause", "effect"}
    blob = " ".join(f"{row['Text']} {row.get('Cue') or ''} {row.get('Role') or ''}" for row in hits).casefold()
    assert "because" in blob or "therefore" in blob or "cause" in blob or "discount" in blob
    rr.close()


def test_rag_relop_plus_chroma_and_mcp(tmp_path, monkeypatch):
    # rag() only consults Chroma when the physical MiniLM path is opted in; the RelOp path never depends on it.
    monkeypatch.setenv("REVOLVERELATE_CHROMA", "1")
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    out = rag(rr, "bookcase binders", strategy="semantic", column="ProductName", n=5)
    assert out["sandboxOnly"] is False
    assert out["relop"]["status"] == "sandbox_ok"
    assert out["relop"]["sql"]
    assert out["relop"]["rows"]
    assert out["relop"]["params"]
    assert out["chroma"]
    listed = dispatch("rr_chroma", {"workdir": str(tmp_path), "dsn": str(live), "action": "status"})
    assert listed.get("available") is True
    asked = dispatch(
        "rr_rag",
        {"workdir": str(tmp_path), "dsn": str(live), "query": "conference chairs", "strategy": "semantic", "n": 4},
    )
    assert asked.get("mode") == "rag"
    assert asked.get("sandboxOnly") is False
    n = rr.sandbox.execute(f'SELECT COUNT(*) FROM "{OVERLAY}" WHERE Strategy IN (\'semantic\', \'causal\')')[1][0][0]
    assert n > 0
    rr.close()
