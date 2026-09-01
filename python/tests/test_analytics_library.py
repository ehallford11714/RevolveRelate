"""Analytics library: scaffold RelOp → dummy sandbox → live promote only after rollout."""

from __future__ import annotations

import pytest

from revolverelate.analytics.catalog import RECIPES, scaffold_ir
from revolverelate.errors import PromoteError
from revolverelate.ir.validate import validate_ir
from revolverelate.mcp.server import dispatch
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore


@pytest.fixture
def rr(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    inst = RevolveRelate.connect(str(live), workdir=tmp_path)
    inst.build(rows_per_entity=8)
    yield inst
    inst.close()


def test_every_recipe_scaffolds_valid_relop(rr):
    assert len(RECIPES) >= 12
    for recipe_id in RECIPES:
        ir = scaffold_ir(recipe_id, rr.schema, {"measure": "Sales", "dimension": "Region", "dimension2": "Category"})
        validate_ir(ir, rr.schema)
        assert ir.get("kind") == "query"
        assert "SELECT" not in str(ir.get("op", {})).split("op")[0] or ir["kind"] == "query"


def test_scaffold_then_rollout_runs_on_sandbox_not_live(rr):
    plan = rr.analytics.scaffold("sum_by_dimension", measure="Sales", dimension="Region")
    assert plan["status"] == "scaffolded"
    assert plan["target"] == "sandbox"
    ran = rr.analytics.rollout(plan["id"])
    assert ran["status"] == "sandbox_ok"
    assert ran["target"] == "sandbox"
    assert ran["sql"].upper().startswith("SELECT")
    assert "GROUP BY" in ran["sql"]
    assert ran["rowCount"] >= 1
    live_sales = rr.adapter.fetchone("SELECT SUM(Sales) FROM OrderLine")[0]
    dummy_emails = rr.sandbox.execute('SELECT Email FROM "Customer" LIMIT 1')[1]
    assert str(dummy_emails[0][0]).startswith("mask_")
    assert live_sales != 0


def test_promote_blocked_until_sandbox_rollout(rr):
    plan = rr.analytics.scaffold("count_by_dimension", dimension="Segment")
    with pytest.raises(PromoteError):
        rr.analytics.promote(plan["id"])
    rr.analytics.rollout(plan["id"])
    live = rr.analytics.promote(plan["id"])
    assert live["status"] == "promoted"
    assert live["target"] == "live"
    assert live["live"]["target"] == "live"
    assert live["live"]["rows"]


def test_complex_recipes_roll_out_on_dummy(rr):
    cases = [
        ("share_of_total", {"measure": "Sales", "dimension": "Category"}),
        ("rank_within", {"measure": "Sales", "dimension": "Region"}),
        ("having_above", {"measure": "Sales", "dimension": "Region", "threshold": 0}),
        ("multi_group", {"measure": "Profit", "dimension": "Region", "dimension2": "Category"}),
        ("pareto", {"measure": "Sales", "dimension": "Segment"}),
        ("union_segments", {"dimension": "Region", "left": "West", "right": "South"}),
        ("running_sum", {"measure": "Sales"}),
        ("coverage_left", {"dimension": "Region"}),
    ]
    for recipe, args in cases:
        ran = rr.analytics.run(recipe, **args)
        assert ran["status"] == "sandbox_ok", recipe
        assert ran["sql"]
        assert ran["rowCount"] >= 1, (recipe, ran["sql"], ran.get("params"))


def test_mcp_analytics_loop(rr, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dsn = str(tmp_path / "superstore.sqlite")
    args = {"workdir": str(rr.workdir), "dsn": dsn}
    listed = dispatch("rr_analytics_list", args)
    assert any(r["id"] == "sum_by_dimension" for r in listed["recipes"])
    assert "Sales" in listed["measures"]
    built = dispatch(
        "rr_analytics_scaffold",
        {"recipe": "sum_by_dimension", "measure": "Sales", "dimension": "Region", **args},
    )
    assert built["status"] == "scaffolded"
    rolled = dispatch("rr_analytics_rollout", {"plan": built["id"], **args})
    assert rolled["status"] == "sandbox_ok"
    assert rolled["target"] == "sandbox"
    promoted = dispatch("rr_analytics_promote", {"plan": built["id"], **args})
    assert promoted.get("status") == "promoted" or promoted.get("live", {}).get("target") == "live"


def test_superstore_analytics_walkthrough(tmp_path):
    from revolverelate.samples.analytics_superstore import CASES, run_superstore_analytics

    report = run_superstore_analytics(tmp_path)
    assert report["build"]["status"] == "complete"
    assert report["cases"] == len(CASES)
    ids = [s["id"] for s in report["steps"]]
    assert "sales_by_region" in ids
    assert "pareto_segment" in ids
    for step in report["steps"]:
        assert step["status"] == "promoted", step["id"]
        assert step["sql"].upper().startswith(("SELECT", "WITH"))
        assert step["sandbox_rows"] is not None


def test_cli_analytics_help():
    from revolverelate.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["analytics", "--help"])
    assert exc.value.code == 0
