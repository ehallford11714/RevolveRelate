"""3) Live push only after a complete saved build cache and sandbox validation."""

from __future__ import annotations

import json

import pytest

from revolverelate.errors import PromoteError, SchemaError
from revolverelate.revolverelate import RevolveRelate


QUERY_IR = {
    "kind": "query",
    "op": {
        "op": "project",
        "items": [{"expr": {"expr": "col", "entity": "Customer", "attr": "LastName"}, "alias": "LastName"}],
        "input": {"op": "scan", "entity": "Customer", "alias": "Customer"},
    },
}


def test_ask_and_promote_blocked_before_build(live_db, tmp_path):
    rr = RevolveRelate.connect(str(live_db), workdir=tmp_path)
    assert rr.cache.load() is None
    with pytest.raises(SchemaError, match="build"):
        rr.ask("customers")
    with pytest.raises(PromoteError, match="No saved build cache"):
        rr.promote(QUERY_IR)
    rr.close()


def test_incomplete_cache_blocks_live_push(live_db, tmp_path):
    rr = RevolveRelate.connect(str(live_db), workdir=tmp_path)
    rr.cache.begin("sqlite")
    rr.cache.mark(schema=True, policy=False, sandbox=False)
    assert not rr.cache.is_complete()
    with pytest.raises(PromoteError, match="not complete"):
        rr.promote(QUERY_IR)
    rr.close()


def test_promote_blocked_until_sandbox_validates(live_db, tmp_path):
    rr = RevolveRelate.connect(str(live_db), workdir=tmp_path)
    rr.build(rows_per_entity=3)
    assert rr.cache.is_complete()
    with pytest.raises(PromoteError, match="Sandbox validation"):
        rr.promote(QUERY_IR)
    rr.close()


def test_full_build_then_sandbox_then_live_query(live_db, tmp_path):
    rr = RevolveRelate.connect(str(live_db), workdir=tmp_path)
    record = rr.build(rows_per_entity=3)
    assert record["status"] == "complete"
    assert rr.cache.build_path.exists()
    sandbox_result = rr.ask("customers")
    assert sandbox_result["target"] == "sandbox"
    assert sandbox_result["validated"] is True
    live = rr.promote(sandbox_result["ir"])
    assert live["target"] == "live"
    assert live["rows"]
    flat = {cell for row in live["rows"] for cell in row}
    assert "Adams" in flat or "Baker" in flat
    rr.close()


def test_second_process_reuses_saved_build_cache(live_db, tmp_path):
    first = RevolveRelate.connect(str(live_db), workdir=tmp_path)
    first.build(rows_per_entity=3)
    first.ask("customers")
    first.close()
    second = RevolveRelate.connect(str(live_db), workdir=tmp_path)
    reused = second.build()
    assert reused["status"] == "complete"
    assert second.cache.is_complete()
    result = second.ask("customers")
    assert result["target"] == "sandbox"
    second.close()


def test_mutate_promote_requires_allow_live_and_capability(live_db, tmp_path):
    rr = RevolveRelate.connect(str(live_db), workdir=tmp_path)
    rr.build(rows_per_entity=3)
    ir = {
        "kind": "mutate",
        "op": {
            "op": "insert",
            "entity": "Customer",
            "columns": ["LastName", "Country"],
            "rows": [["Lovelace", "UK"]],
        },
    }
    rr.sandbox.begin()
    rr.sandbox.run_ir(ir)
    rr.sandbox.commit()
    key = json.dumps(ir, sort_keys=True)
    rr._validated[key] = True
    rr._save_validation(key, ir, "INSERT")
    with pytest.raises(PromoteError, match="allow_live"):
        rr.promote(ir)
    with pytest.raises(PromoteError, match="mutate_live"):
        rr.promote(ir, allow_live=True)
    rr.close()
