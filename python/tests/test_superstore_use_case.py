"""Intended Superstore loop: install/build, auto-chunk, ask any question, causal RAG, live replay.

Dummy stages the RelOp. The same plan then runs on live Superstore. OverlayChunk is virtual
(not a business table). Demo notes stay on dummy. No Chroma import.
"""

from __future__ import annotations

from revolverelate.analytics.primitives import get_composite
from revolverelate.mcp.server import dispatch, route_question
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore
from revolverelate.vector.overlay import OVERLAY, install_overlay_live


def _boot(tmp_path, rows: int = 6):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=rows)
    return live, rr


def test_route_question_picks_ask_rag_causal_pearl():
    assert route_question("customers in West") == "ask"
    assert route_question("nearest bookcase binders") == "rag"
    assert route_question("sales fell because discounting") == "causal"
    assert route_question("what if West discount were zero") == "pearl"


def test_superstore_build_parses_schema_and_chunks_overlay(tmp_path):
    live, rr = _boot(tmp_path)
    assert {e.name for e in rr.schema.all_entities()} == {"Customer", "Product", "Orders", "OrderLine"}
    assert OVERLAY not in {e.name for e in rr.schema.all_entities()}
    assert rr.schema.entity(OVERLAY) is not None
    stats = rr.overlay_stats()
    assert stats["chunks"] >= 1
    for field in ("Cue", "Role", "Text", "SourcePk", "Strategy", "Hash"):
        assert field in stats["fields"]
    assert any(row["column"] == "ProductName" for row in stats["textColumns"])
    dummy_blob = " ".join(
        str(v)
        for row in rr.sandbox.execute(f'SELECT Text FROM "{OVERLAY}"')[1]
        for v in row
    ).casefold()
    assert "because" in dummy_blob or "discount" in dummy_blob
    live_n = install_overlay_live(rr.adapter, rr.schema, rr.policy)
    assert live_n >= 1
    live_blob = " ".join(
        str(v)
        for row in rr.adapter.fetchall(f'SELECT Text FROM "{OVERLAY}"')
        for v in row
    ).casefold()
    assert "sales fell because" not in live_blob
    assert "bookcase" in live_blob or "chair" in live_blob or "phone" in live_blob
    rr.close()
    assert live.exists()


def test_superstore_any_business_question_dummy_then_live(tmp_path):
    _live, rr = _boot(tmp_path)
    asked = rr.ask("customers in West")
    assert asked["target"] == "sandbox"
    assert asked["validated"] is True
    assert asked["sql"].upper().startswith("SELECT")
    assert asked["rows"]
    live_out = rr.replay_live(ir=asked["ir"])
    assert live_out["ran"] is True
    assert live_out["target"] == "live"
    assert (live_out["rowCount"] or 0) >= 1
    names = {str(cell) for row in (live_out["rows"] or []) for cell in row}
    assert "Claire Gute" not in names
    rr.close()


def test_superstore_semantic_rag_dummy_then_live(tmp_path):
    _live, rr = _boot(tmp_path)
    out = rr.rag("bookcase binders", strategy="semantic", live=True)
    assert out["sandboxOnly"] is False
    assert out["relop"]["status"] == "sandbox_ok"
    assert (out["relop"]["rowCount"] or 0) >= 1
    assert out["live"]["ran"] is True
    cols = [str(c).casefold() for c in (out["live"].get("columns") or [])]
    assert any("text" in c or "source" in c or "hash" in c for c in cols)
    rr.close()


def test_superstore_causal_rag_relops_dummy_then_live(tmp_path):
    _live, rr = _boot(tmp_path)
    for cid in ("rag_causal_knn", "rag_causal_pair", "causal_then_agg", "causal_then_intervene"):
        spec = get_composite(cid)
        assert spec.get("sandboxOnly") is not True
        plan = rr.analytics.run_chain(composite=cid)
        assert plan["status"] == "sandbox_ok", cid
        live_out = rr.replay_live(plan_id=plan["id"])
        assert live_out["ran"] is True, (cid, live_out)
        assert live_out.get("sql")
        assert "UPDATE" not in str(live_out.get("sql") or "").upper()
    causal = rr.causal("sales fell because discounting", live=True)
    assert causal["sandboxOnly"] is False
    assert causal["relop"]["status"] == "sandbox_ok"
    assert causal["live"]["ran"] is True
    rr.close()


def test_superstore_pearl_glm_and_case_on_live(tmp_path):
    _live, rr = _boot(tmp_path)
    out = rr.pearl("what if West discount were zero", live=True, discourse=False)
    assert out["identify"]["criterion"] == "backdoor"
    assert out["live"]["facts"]["ran"] is True
    assert out["live"]["do"]["ran"] is True
    sql = str(out["live"]["do"].get("sql") or "")
    assert "case" in sql.casefold()
    assert "update" not in sql.casefold()
    rr.close()


def test_mcp_superstore_agent_loop(tmp_path, monkeypatch):
    live = write_superstore(tmp_path / "superstore.sqlite")
    monkeypatch.chdir(tmp_path)
    args = {"dsn": str(live), "workdir": str(tmp_path), "rows": 6}

    boot = dispatch("rr_boot", args)
    assert boot.get("ok") is True
    assert boot.get("complete") is True
    assert (boot.get("overlay") or {}).get("chunks", 0) >= 1
    assert "Cue" in ((boot.get("overlay") or {}).get("fields") or [])

    asked = dispatch("rr_question", {**args, "question": "customers in West"})
    assert asked.get("error") is None, asked
    assert asked["mode"] == "ask"
    assert asked["routed"] == "ask"
    assert asked["target"] == "sandbox"
    assert asked.get("promoted") is True
    assert asked.get("live", {}).get("ran") is True

    rag = dispatch("rr_question", {**args, "question": "nearest bookcase binders"})
    assert rag.get("error") is None, rag
    assert rag.get("routed") == "rag" or rag.get("mode") == "rag"
    assert rag.get("sandboxOnly") is False
    assert rag.get("live", {}).get("ran") is True

    causal = dispatch("rr_causal", {**args, "question": "sales fell because discounting"})
    assert causal.get("error") is None, causal
    assert causal.get("mode") == "causal"
    assert causal.get("sandboxOnly") is False
    assert causal.get("live", {}).get("ran") is True

    pearl = dispatch("rr_pearl", {**args, "question": "what if West discount were zero", "discourse": False})
    assert pearl.get("error") is None, pearl
    assert pearl.get("mode") == "pearl"
    assert pearl.get("live", {}).get("do", {}).get("ran") is True
    assert pearl.get("live", {}).get("facts", {}).get("ran") is True
