"""Socratic objective, AskLog, intervene worlds, and RelOp ideation."""

from __future__ import annotations

from revolverelate.analytics.asklog import ASKLOG
from revolverelate.analytics.intent_apply import match_composite, match_templates
from revolverelate.analytics.primitives import get_composite, list_families
from revolverelate.ir.validate import validate_ir
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore
from revolverelate.vector.overlay import OVERLAY


def test_socratic_ties_objective_to_composites():
    rows = match_templates("west sales and nearest bookcase retrieve")
    ids = {r["composite"] for r in rows}
    assert "west_sales_by_category" in ids
    assert "rag_then_agg" in ids
    assert match_composite("what if discount were zero") == "intervene_west_discount"


def test_families_include_intent_world_search():
    assert {"intent", "world", "search", "vector"} <= {f["id"] for f in list_families()}


def test_rag_then_agg_is_a_question(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    spec = get_composite("rag_then_agg")
    assert spec.get("sandboxOnly") is not True
    plan = rr.analytics.run_chain(composite="rag_then_agg")
    assert plan["status"] == "sandbox_ok"
    assert plan["sql"]
    assert {e.name for e in rr.schema.all_entities()} == {"Customer", "Product", "Orders", "OrderLine"}
    assert rr.schema.entity(OVERLAY) is not None
    rr.close()


def test_asklog_is_virtual_and_scannable(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    assert rr.schema.entity(ASKLOG) is not None
    assert ASKLOG not in {e.name for e in rr.schema.all_entities()}
    rr.analytics.run_chain(composite="west_sales_by_category")
    ir = rr.analytics.scaffold_chain(composite="socratic_west")["ir"]
    validate_ir(ir, rr.schema)
    logged = rr.sandbox.execute(f'SELECT COUNT(*) FROM "{ASKLOG}"')[1][0][0]
    assert logged > 0
    plan = rr.analytics.run_chain([{"op": "ask_log"}])
    assert plan["status"] == "sandbox_ok"
    assert plan["rowCount"] >= 1
    rr.close()


def test_intervene_is_query_not_update(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    spec = get_composite("intervene_west_discount")
    assert spec.get("sandboxOnly") is not True
    plan = rr.analytics.run_chain(composite="intervene_west_discount")
    assert plan["status"] == "sandbox_ok"
    assert "UPDATE" not in (plan.get("sql") or "").upper()
    assert plan["columns"]
    rr.close()


def test_hypothesize_names_a_dummy_view(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    named = rr.analytics.hypothesize(composite="west_sales_by_category", name="WestView")
    assert named["status"] == "sandbox_ok"
    assert named["name"] == "WestView"
    assert "WITH" in (named.get("sql") or "").upper()
    rr.close()


def test_explore_abduces_a_winner(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    out = rr.analytics.explore("west sales")
    assert out["kind"] == "explore"
    assert out["socratic"]
    assert out["candidates"]
    assert out["winner"]["composite"]
    assert out["winner"]["status"] == "sandbox_ok"
    abduced = rr.analytics.abduce("loss profit")
    assert abduced["winner"]["composite"] == "loss_makers"
    rr.close()
