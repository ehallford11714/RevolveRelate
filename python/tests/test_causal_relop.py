"""Causal RelOp: pair, attach, intervene, vs_world. SLM plans primitives only."""

from __future__ import annotations

from revolverelate.analytics.asklog import ASKLOG
from revolverelate.analytics.causal_plan import (
    causal_candidates,
    fallback_causal_plan,
    match_causal_composite,
    score_causal_rows,
)
from revolverelate.analytics.composites import check_chain
from revolverelate.analytics.primitives import get_composite
from revolverelate.ir.validate import validate_ir
from revolverelate.mcp.server import dispatch
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore


def test_causal_composites_obey_grammar():
    for cid in ("rag_causal_pair", "causal_then_agg", "causal_then_intervene"):
        spec = get_composite(cid)
        assert spec.get("sandboxOnly") is not True
        report = check_chain(spec["steps"])
        assert report["ok"], (cid, report["issues"])


def test_fallback_plan_picks_intervene_and_pair():
    inter = fallback_causal_plan("what if we stopped discounting in the West")
    assert inter["kind"] == "causal_plan"
    assert inter["composite"] == "causal_then_intervene"
    assert inter["grammar"]["ok"]
    assert any(s["op"] == "vs_world" for s in inter["steps"])
    pair = fallback_causal_plan("why did sales fall because discounting")
    assert pair["composite"] == "rag_causal_pair"
    assert any(s["op"] == "pair_causal" for s in pair["steps"])
    assert match_causal_composite("nearest chair") == "rag_causal_knn"


def test_rag_causal_pair_returns_because_edges(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=6)
    plan = rr.analytics.run_chain(composite="rag_causal_pair")
    assert plan["status"] == "sandbox_ok"
    assert plan["sql"]
    validate_ir(plan["ir"], rr.schema)
    blob = " ".join(str(c) for c in (plan.get("columns") or [])).casefold()
    assert "cause" in blob or "effect" in blob or "cue" in blob
    rows = plan.get("rows") or []
    text = " ".join(" ".join(str(v) for v in row) for row in rows).casefold()
    assert "because" in text or "discount" in text or "sales fell" in text
    rr.close()


def test_causal_then_intervene_vs_world(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=6)
    plan = rr.analytics.run_chain(composite="causal_then_intervene")
    assert plan["status"] == "sandbox_ok"
    sql = (plan.get("sql") or "").upper()
    assert "CASE" in sql
    cols = [str(c).casefold() for c in (plan.get("columns") or [])]
    assert any("observed" in c for c in cols)
    assert any("intervened" in c for c in cols)
    assert (plan.get("rowCount") or 0) > 0
    blob = " ".join(" ".join(str(v) for v in row) for row in (plan.get("rows") or [])).casefold()
    assert "office" in blob or "furniture" in blob or "technolog" in blob
    agg = rr.analytics.run_chain(composite="causal_then_agg")
    assert agg["status"] == "sandbox_ok"
    assert (agg.get("rowCount") or 0) > 0
    rr.close()


def test_why_note_attaches_to_a_west_product(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=6)
    from revolverelate.vector.overlay import OVERLAY

    cols, rows = rr.sandbox.execute(
        f'''SELECT o.SourcePk, p.ProductId, c.Region, substr(o.Text,1,40)
            FROM "{OVERLAY}" o
            JOIN Product p ON CAST(p.ProductId AS TEXT) = o.SourcePk
            JOIN OrderLine ol ON ol.ProductId = p.ProductId
            JOIN Orders ord ON ord.OrderId = ol.OrderId
            JOIN Customer c ON c.CustomerId = ord.CustomerId
            WHERE o.Strategy = 'causal' AND o.Cue = 'because' AND o.Role = 'cause'
            LIMIT 8'''
    )
    assert rows, "because-cause note must join a dummy Product on a West (or any) order line"
    regions = {str(r[2]) for r in rows}
    assert "West" in regions
    assert {e.name for e in rr.schema.all_entities()} == {"Customer", "Product", "Orders", "OrderLine"}
    rr.close()


def test_causal_api_and_mcp(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    out = rr.causal("sales fell because discounting")
    assert out["sandboxOnly"] is False
    assert out["kind"] == "causal_plan"
    assert out.get("live", {}).get("ran") is True
    assert out["relop"]["status"] == "sandbox_ok"
    assert out["steps"]
    asked = dispatch(
        "rr_causal",
        {"workdir": str(tmp_path), "dsn": str(live), "question": "what if West discount were zero because discounting"},
    )
    assert asked.get("mode") == "causal"
    assert asked.get("sandboxOnly") is False
    assert any(s.get("op") == "vs_world" for s in (asked.get("steps") or []))
    rr.close()


def test_score_causal_rows_uses_world_delta_not_sales_volume():
    pair = score_causal_rows(["Cause", "Effect"], [["a", "b"], ["c", "d"]])
    world = score_causal_rows(["Category", "observed", "intervened"], [["Furniture", 0.2, 0.0]])
    empty = score_causal_rows(["Category", "observed", "intervened"], [])
    assert pair == 2
    assert empty == 0
    assert world > pair
    assert "intervene_west_discount" in causal_candidates()


def test_causal_explore_abduces_winner_and_asklog(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    out = rr.causal_explore("sales fell because discounting")
    assert out["kind"] == "causal_explore"
    assert out["sandboxOnly"] is False
    assert out["goal"]["measure"]
    ids = [row["composite"] for row in out["candidates"]]
    assert len(ids) >= 2
    assert out["composite"] in ids
    assert out["winner"]["composite"] == out["composite"]
    assert out["relop"]["status"] == "sandbox_ok"
    assert out["candidates"]
    n = rr.sandbox.execute(f'SELECT COUNT(*) FROM "{ASKLOG}" WHERE Pattern = ?', ["causal_abduce"])[1][0][0]
    assert n >= 2
    asked = dispatch(
        "rr_causal_explore",
        {"workdir": str(tmp_path), "dsn": str(live), "question": "why did sales fall because discounting"},
    )
    assert asked.get("mode") == "causal_explore"
    assert asked.get("sandboxOnly") is False
    assert asked.get("composite")
    assert len(asked.get("candidates") or []) >= 2
    again = rr.causal_explore("sales fell because discounting")
    assert again.get("memory")
    assert any(row.get("composite") for row in again["memory"])
    assert {e.name for e in rr.schema.all_entities()} == {"Customer", "Product", "Orders", "OrderLine"}
    rr.close()
