"""2) A local duplicate sandbox DB can be created from the schema (dummy data, not a full live load)."""

from __future__ import annotations

from revolverelate.policy.accept import accept_policy, default_policy
from revolverelate.revolverelate import RevolveRelate
from revolverelate.sandbox.engine import Sandbox
from revolverelate.schema.dummy import generate_dummy_rows


def test_dummy_rows_follow_schema_and_mask_critical(schema):
    policy = accept_policy(default_policy(schema), schema)
    dummy = generate_dummy_rows(schema, rows_per_entity=4, reveal=set())
    customers = dummy["Customer"]
    assert len(customers) == 4
    assert customers[0]["CustomerId"] == 1
    assert str(customers[0]["Email"]).startswith("mask_")
    assert str(customers[0]["Password"]).startswith("mask_")
    invoices = dummy["Invoice"]
    assert invoices[0]["CustomerId"] in {row["CustomerId"] for row in customers}


def test_sandbox_creates_local_dup_file(schema, tmp_path):
    policy = accept_policy(default_policy(schema), schema)
    path = tmp_path / "sandbox.sqlite"
    box = Sandbox(path, schema, policy).create(rows_per_entity=5)
    assert path.exists()
    assert box.table_names() == ["Customer", "Invoice"]
    assert box.row_count("Customer") == 5
    assert box.row_count("Invoice") == 5
    cols, rows = box.execute('SELECT Email, Password FROM "Customer" LIMIT 1')
    assert rows[0][0].startswith("mask_")
    assert rows[0][1].startswith("mask_")
    box.close()


def test_sandbox_executes_compiled_sql(schema, tmp_path):
    policy = accept_policy(default_policy(schema), schema)
    box = Sandbox(tmp_path / "sb.sqlite", schema, policy).create()
    ir = {
        "kind": "query",
        "op": {
            "op": "project",
            "items": [{"expr": {"expr": "col", "entity": "Customer", "attr": "LastName"}, "alias": "LastName"}],
            "input": {"op": "scan", "entity": "Customer", "alias": "Customer"},
        },
    }
    sql, params, columns, rows = box.run_ir(ir)
    assert "SELECT" in sql
    assert columns == ["LastName"]
    assert len(rows) == 8
    box.close()


def test_build_writes_local_sandbox_not_live_passwords(live_db, tmp_path):
    rr = RevolveRelate.connect(str(live_db), workdir=tmp_path)
    record = rr.build(rows_per_entity=3)
    assert record["status"] == "complete"
    assert rr.cache.sandbox_path.exists()
    cols, rows = rr.sandbox.execute('SELECT Password FROM "Customer"')
    assert rows
    assert all(str(r[0]).startswith("mask_") for r in rows)
    live_pw = rr.adapter.fetchall("SELECT Password FROM Customer")
    assert live_pw[0][0] == "secret1"
    rr.close()
