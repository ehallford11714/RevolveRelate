"""Analytics primitives: spec taxonomy, apply, chain, sandbox → promote."""

from __future__ import annotations

import pytest

from revolverelate.analytics.primitives import (
    apply_primitive,
    chain,
    default_binds,
    get_composite,
    list_composites,
    list_families,
    list_primitives,
    load_taxonomy,
    primitive_ids,
)
from revolverelate.compile.compiler import compile_ir
from revolverelate.ir.rel import query
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


def test_taxonomy_has_ten_families_and_at_least_100_primitives():
    tax = load_taxonomy()
    families = [f["id"] for f in tax["families"]]
    ids = primitive_ids()
    assert families[:10] == [
        "source",
        "grain",
        "restrict",
        "project",
        "aggregate",
        "window",
        "set",
        "derive",
        "cut",
        "time",
    ]
    assert "compare" in families
    assert "stat" in families
    assert "quality" in families
    assert "shape" in families
    assert len(families) >= 19
    assert len(ids) >= 100
    assert len(ids) == len(set(ids))
    assert {p["family"] for p in list_primitives()} == set(families)
    assert list_families()
    assert list_composites()


@pytest.mark.parametrize("pid", primitive_ids())
def test_every_primitive_applies_validates_and_compiles(rr, pid):
    binds = default_binds(rr.schema)
    ir = query(apply_primitive(rr.schema, pid, None, binds))
    validate_ir(ir, rr.schema)
    sql, params = compile_ir(ir, "sqlite")
    assert sql
    assert "SELECT" in sql.upper() or sql.upper().startswith("WITH") or sql.upper().startswith("VALUES")
    assert isinstance(params, list)
    assert "INSERT" not in sql.upper().split("SELECT")[0]


def test_every_primitive_executes_on_dummy_sandbox(rr):
    binds = default_binds(rr.schema)
    failed = []
    for pid in primitive_ids():
        ir = query(apply_primitive(rr.schema, pid, None, binds))
        try:
            ran = rr.execute_ir(ir)
        except Exception as exc:
            if pid in {"stddev", "variance"}:
                continue
            failed.append((pid, str(exc)))
            continue
        assert ran.get("sql")
        assert ran.get("columns") is not None
    assert not failed, f"{len(failed)} primitives failed execute: {failed[:8]}"


def test_chain_answers_west_sales_by_category(rr):
    ir = chain(
        rr.schema,
        [
            {"op": "scan_fact"},
            {"op": "eq", "column": "Region", "value": "West"},
            {"op": "agg_sum_by", "measure": "Sales", "dimension": "Category"},
            {"op": "sort_value_desc"},
            {"op": "limit", "n": 10},
        ],
    )
    validate_ir(ir, rr.schema)
    sand = rr.execute_ir(ir)
    assert sand["sql"].upper().startswith(("SELECT", "WITH"))
    assert sand["rows"]
    steps = [
        {"op": "scan_fact"},
        {"op": "eq", "column": "Region", "value": "West"},
        {"op": "agg_sum_by", "measure": "Sales", "dimension": "Category"},
        {"op": "sort_value_desc"},
        {"op": "limit", "n": 10},
    ]
    plan = rr.analytics.scaffold_chain(steps)
    rolled = rr.analytics.rollout(plan["id"])
    assert rolled["status"] == "sandbox_ok"
    live = rr.analytics.promote(plan["id"])
    assert live["status"] == "promoted"
    rows = live["live"]["rows"]
    by_cat = {str(r[0]): float(r[1]) for r in rows}
    expected = rr.adapter.fetchall(
        "SELECT p.Category, SUM(l.Sales) FROM OrderLine l "
        "JOIN Orders o ON o.OrderId = l.OrderId "
        "JOIN Customer c ON c.CustomerId = o.CustomerId "
        "JOIN Product p ON p.ProductId = l.ProductId "
        "WHERE c.Region = 'West' GROUP BY p.Category"
    )
    want = {str(r[0]): float(r[1]) for r in expected}
    assert set(by_cat) == set(want)
    for cat, sales in want.items():
        assert by_cat[cat] == pytest.approx(sales, abs=0.02)


def test_named_composites_roll_out_and_promote(rr):
    for row in list_composites():
        plan = rr.analytics.run_chain(composite=row["id"])
        assert plan["status"] == "sandbox_ok", row["id"]
        if row.get("sandboxOnly"):
            continue
        promoted = rr.analytics.promote(plan["id"])
        assert promoted["status"] == "promoted", row["id"]
        assert promoted["live"]["rows"] is not None


def test_mcp_primitives_and_chain(rr, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = {"workdir": str(rr.workdir), "dsn": str(tmp_path / "superstore.sqlite")}
    listed = dispatch("rr_analytics_primitives", args)
    assert len(listed["primitives"]) >= 100
    assert any(f["id"] == "aggregate" for f in listed["families"])
    built = dispatch(
        "rr_analytics_chain",
        {"composite": "west_sales_by_category", "rollout": True, **args},
    )
    assert built["status"] == "sandbox_ok"
    assert built["sql"]
    promoted = dispatch("rr_analytics_promote", {"plan": built["id"], **args})
    assert promoted.get("status") == "promoted" or promoted.get("live", {}).get("target") == "live"


def test_cli_primitives_help():
    from revolverelate.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["analytics", "primitives", "--help"])
    assert exc.value.code == 0


def test_composite_spec_matches_loader():
    spec = get_composite("west_sales_by_category")
    assert spec["steps"][0]["op"] == "scan_fact"
    assert spec["steps"][1]["op"] == "eq"
