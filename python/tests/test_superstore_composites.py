"""100 Superstore composite RelOps, plus the same questions through local Qwen 27B."""

from __future__ import annotations

import pytest

from revolverelate.compile.compiler import compile_ir
from revolverelate.slm import probe as slm_probe
from revolverelate.ir.nl import question_to_relop
from revolverelate.ir.validate import validate_ir
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.composites import CASES, SLM_QUESTIONS
from revolverelate.samples.superstore import write_superstore
from revolverelate.slm.jobs import fill_relop, schema_card
from revolverelate.slm.probe import probe_slm

QWEN_SMOKE = [
    "customers in West",
    "orders in California",
    "orderlines over 500",
    "products in Technology",
    "count customers by region",
]


@pytest.fixture(scope="module")
def superstore_rr(tmp_path_factory):
    root = tmp_path_factory.mktemp("ss_comp")
    live = write_superstore(root / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=root)
    rr.build(rows_per_entity=8)
    yield rr
    rr.close()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_composite_relop_on_superstore(superstore_rr, case):
    ir = case["ir"]
    validate_ir(ir, superstore_rr.schema)
    sql, params = compile_ir(ir, "sqlite")
    assert case["sql_has"] in sql, sql
    cols, rows = superstore_rr.adapter.execute(sql, params)
    assert len(rows) >= case["min_rows"], (case["name"], sql, params, rows)
    assert cols or rows == []


def test_there_are_one_hundred_composites():
    assert len(CASES) == 100
    assert len(SLM_QUESTIONS) == 100


@pytest.mark.parametrize("question", SLM_QUESTIONS)
def test_question_compiles_on_superstore(superstore_rr, question):
    ir = question_to_relop(question, superstore_rr.schema)
    validate_ir(ir, superstore_rr.schema)
    sql, params = compile_ir(ir, "sqlite")
    assert sql.upper().startswith(("SELECT", "WITH"))
    superstore_rr.adapter.execute(sql, params)


@pytest.fixture
def qwen_env(monkeypatch):
    monkeypatch.setenv("REVOLVERELATE_SLM", "auto")
    monkeypatch.setenv("REVOLVERELATE_SLM_MODEL", "qwen3.8:27b")
    slm_probe._CACHE = None
    yield
    slm_probe._CACHE = None


def test_qwen_27b_is_available(qwen_env):
    slm = probe_slm(force=True)
    assert slm.available, slm.reason
    assert slm.model, slm.to_dict()
    assert "27b" in slm.model.lower() or slm.model == "qwen3.8:27b"


@pytest.mark.parametrize("question", QWEN_SMOKE)
def test_qwen27b_smoke_fills_relop(superstore_rr, qwen_env, question):
    slm = probe_slm(force=True)
    if not slm.available:
        pytest.fail(f"Qwen 27B is not available: {slm.reason}")
    ir = fill_relop(question, superstore_rr.schema, superstore_rr.policy, fallback=False)
    assert ir.get("kind") in {"query", "mutate", "txn"}
    validate_ir(ir, superstore_rr.schema)
    sql, params = compile_ir(ir, "sqlite")
    assert sql.upper().startswith(("SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "BEGIN"))
    superstore_rr.adapter.execute(sql, params)


def test_schema_card_hides_email_from_qwen(superstore_rr):
    card = schema_card(superstore_rr.schema, superstore_rr.policy)
    assert "Customer" in card
    assert "Email" not in card
