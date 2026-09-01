"""1) With a provided schema, SQL is capable of being written."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from revolverelate.compile.compiler import compile_ir
from revolverelate.errors import SchemaError
from revolverelate.ir.nl import question_to_relop
from revolverelate.ir.validate import validate_ir
from revolverelate.schema.model import SchemaGraph

SPEC = Path(__file__).resolve().parents[2] / "spec" / "fixtures"
DIALECTS = ("postgres", "sqlite", "duckdb", "mysql", "tds", "snowflake", "bigquery")


def test_provided_schema_compiles_scan_to_sql(schema):
    ir = {
        "kind": "query",
        "op": {
            "op": "project",
            "items": [
                {"expr": {"expr": "col", "entity": "Customer", "attr": "LastName"}, "alias": "LastName"}
            ],
            "input": {"op": "scan", "entity": "Customer", "alias": "Customer"},
        },
    }
    validate_ir(ir, schema)
    sql, params = compile_ir(ir, "sqlite")
    assert "SELECT" in sql
    assert "Customer" in sql
    assert "LastName" in sql
    assert params == []


def test_provided_schema_compiles_join_filter(schema):
    ir = question_to_relop("invoices in Canada", schema)
    validate_ir(ir, schema)
    sql, params = compile_ir(ir, schema.engine)
    assert "JOIN" in sql
    assert "Invoice" in sql and "Customer" in sql
    assert params == ["Canada"]


def test_unknown_entity_rejected(schema):
    ir = {"kind": "query", "op": {"op": "scan", "entity": "Nope", "alias": "Nope"}}
    with pytest.raises(SchemaError):
        validate_ir(ir, schema)


def test_nl_never_emits_sql_directly(schema):
    ir = question_to_relop("customers in Canada", schema)
    assert ir["kind"] == "query"
    assert ir["op"]["op"] == "limit"
    assert "SELECT" not in json.dumps(ir)


@pytest.mark.parametrize("name", ["scan-project", "filter-join", "aggregate", "insert", "setop"])
@pytest.mark.parametrize("dialect", DIALECTS)
def test_golden_sql_from_schema_bound_ir(name: str, dialect: str):
    fixture = json.loads((SPEC / f"{name}.json").read_text(encoding="utf-8"))
    sql, params = compile_ir(fixture["ir"], dialect)
    assert sql == fixture["sql"][dialect]
    assert params == fixture["params"][dialect]


def test_schema_graph_roundtrip_still_compiles(schema):
    restored = SchemaGraph.from_dict(schema.to_dict())
    ir = question_to_relop("customers", restored)
    validate_ir(ir, restored)
    sql, _ = compile_ir(ir, "postgres")
    assert sql.startswith("SELECT")
    assert '"Customer"' in sql
