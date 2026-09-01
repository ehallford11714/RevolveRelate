"""Live Superstore examples: schema → dummy sandbox → RelOp → SQL → promote."""

from __future__ import annotations

import sqlite3

from revolverelate.compile.compiler import compile_ir
from revolverelate.ir.nl import question_to_relop
from revolverelate.ir.validate import validate_ir
from revolverelate.mcp.server import dispatch
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore
from revolverelate.samples.walkthrough import run_superstore_example


def test_superstore_schema_writes_sql(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    names = {e.name for e in rr.schema.all_entities()}
    assert names == {"Customer", "Product", "Orders", "OrderLine"}
    ir = question_to_relop("customers in West", rr.schema)
    validate_ir(ir, rr.schema)
    sql, params = compile_ir(ir, "sqlite")
    assert "SELECT" in sql
    assert "Customer" in sql
    assert params == ["West"]
    assert "JOIN" not in sql
    rr.close()


def test_superstore_orders_join_state(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    ir = question_to_relop("orders in California", rr.schema)
    validate_ir(ir, rr.schema)
    sql, params = compile_ir(ir, "sqlite")
    assert "JOIN" in sql
    assert "Customer" in sql and "Orders" in sql
    assert params == ["California"]
    rr.close()


def test_superstore_dummy_sandbox_masks_email(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=5)
    live_email = rr.adapter.fetchone("SELECT Email FROM Customer WHERE CustomerId = 1")[0]
    assert live_email == "claire.gute@example.com"
    _cols, rows = rr.sandbox.execute('SELECT Email FROM "Customer" LIMIT 3')
    assert rows
    assert all(str(r[0]).startswith("mask_") for r in rows)
    assert rr.cache.sandbox_path.exists()
    rr.close()


def test_superstore_ask_sales_over_500_on_sandbox(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    result = rr.ask("orderlines over 500")
    assert result["target"] == "sandbox"
    assert result["validated"] is True
    assert "Sales" in result["sql"]
    assert result["params"] == [500.0]
    assert result["rows"]
    rr.close()


def test_superstore_promote_west_customers_to_live(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    asked = rr.ask("customers in West")
    live_result = rr.promote(asked["ir"])
    assert live_result["target"] == "live"
    conn = sqlite3.connect(str(live))
    west = {row[0] for row in conn.execute("SELECT CustomerName FROM Customer WHERE Region = 'West'")}
    conn.close()
    names = {cell for row in live_result["rows"] for cell in row}
    assert west & names
    assert "Claire Gute" not in names
    rr.close()


def test_superstore_full_walkthrough(tmp_path):
    report = run_superstore_example(tmp_path)
    assert report["build"]["status"] == "complete"
    assert report["live_email_sample"] == "claire.gute@example.com"
    assert str(report["dummy_email_sample"]).startswith("mask_")
    questions = [step["question"] for step in report["steps"]]
    assert questions == [
        "customers in West",
        "orders in California",
        "orderlines over 500",
        "products in Technology",
    ]
    for step in report["steps"]:
        assert step["sql"].startswith("SELECT")
        assert step["target"] == "sandbox"
        assert step["ir"]["kind"] == "query"
        assert step["sandbox_rows"], step["question"]
    assert report["promote"]["target"] == "live"
    assert report["promote"]["rows"]


def test_superstore_mcp_loop(tmp_path, monkeypatch):
    live = write_superstore(tmp_path / "superstore.sqlite")
    monkeypatch.chdir(tmp_path)
    health = dispatch("rr_health", {"workdir": str(tmp_path)})
    assert health["complete"] is False
    built = dispatch("rr_build", {"dsn": str(live), "workdir": str(tmp_path), "rows": 4})
    assert built["status"] == "complete"
    asked = dispatch("rr_ask", {"question": "customers in West", "dsn": str(live), "workdir": str(tmp_path)})
    assert asked["target"] == "sandbox"
    assert asked.get("error") is None
    promoted = dispatch(
        "rr_promote",
        {"ir": asked["ir"], "dsn": str(live), "workdir": str(tmp_path)},
    )
    assert promoted.get("target") == "live"
    engines = dispatch("rr_engines", {})
    assert engines["count"] >= 100
