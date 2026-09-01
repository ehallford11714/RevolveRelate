"""Pearl backdoor, GLM, and do() CASE on dummy then live. No Chroma."""

from __future__ import annotations

from revolverelate.analytics.composites import check_chain
from revolverelate.analytics.pearl import backdoor_ate, identify, load_pearl_spec
from revolverelate.analytics.primitives import get_composite
from revolverelate.mcp.server import dispatch
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore


def test_identify_backdoor_from_spec():
    spec = load_pearl_spec()
    got = identify({"treatment": "Discount", "outcome": "Sales"})
    assert spec["identification"] == "backdoor"
    assert got["criterion"] == "backdoor"
    assert got["identifiable"] is True
    assert got["treatment"] == "Discount"
    assert got["outcome"] == "Sales"
    assert got["adjustment"] == ["Category"]
    assert "Σ" in (got["formula"] or "") or "P(Z" in (got["formula"] or "")


def test_backdoor_ate_recovers_constant_stratum_effect():
    columns = ["Category", "Discount", "Sales"]
    rows = [
        ["Furniture", 1, 10],
        ["Furniture", 1, 10],
        ["Furniture", 10, 20],
        ["Office", 1, 5],
        ["Office", 10, 15],
    ]
    got = backdoor_ate(columns, rows, "Discount", "Sales", ["Category"])
    assert got["nUsed"] == 5
    assert got["ate"] == 10
    assert not got["positivitySkipped"]
    assert got["glm"]["oddsRatio"] is not None


def test_pearl_composites_are_live_promoteable():
    for cid in ("pearl_backdoor_facts", "pearl_do_west"):
        spec = get_composite(cid)
        assert spec.get("sandboxOnly") is not True
        assert check_chain(spec["steps"])["ok"]
        assert not any(s.get("op") in {"overlay", "knn", "chunk_causal"} for s in spec["steps"])


def test_pearl_glm_and_case_run_dummy_then_live(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=8)
    assert {e.name for e in rr.schema.all_entities()} == {"Customer", "Product", "Orders", "OrderLine"}
    out = rr.pearl("what if West discount were zero", live=True, discourse=False)
    assert out["kind"] == "pearl"
    assert out["overlayPromoted"] is False
    assert out["sandboxOnly"] is False
    ident = out["identify"]
    assert ident["criterion"] == "backdoor"
    assert ident["adjustment"] == ["Category"]
    assert out["bind"]["treatment"] == "Discount"
    assert out["bind"]["outcome"] == "Sales"
    dummy_ate = out["sandbox"]["ate"]
    assert dummy_ate["n"] >= 1
    assert dummy_ate["glm"]["oddsRatio"] is not None
    dummy_glm = out["sandbox"]["glm"]
    assert dummy_glm
    assert any(c.get("treatment") == "Discount" for c in dummy_glm)
    world = out["sandbox"]["do"]
    assert world["status"] == "sandbox_ok"
    cols = [str(c).casefold() for c in world.get("columns") or []]
    assert "observed" in cols
    assert "intervened" in cols
    sql = str(world.get("sql") or "")
    assert "case" in sql.casefold()
    assert "update" not in sql.casefold()
    live_facts = out["live"]["facts"]
    assert live_facts.get("ran") is True
    assert (live_facts.get("rowCount") or 0) >= 1
    assert live_facts.get("ate")
    assert live_facts["ate"]["glm"]["oddsRatio"] is not None
    live_do = out["live"]["do"]
    assert live_do.get("ran") is True
    assert (live_do.get("rowCount") or 0) >= 1
    live_cols = [str(c).casefold() for c in live_do.get("columns") or []]
    assert "observed" in live_cols
    assert "intervened" in live_cols
    live_sql = str(live_do.get("sql") or "")
    assert "case" in live_sql.casefold()
    assert "update" not in live_sql.casefold()
    asked = dispatch(
        "rr_pearl",
        {
            "workdir": str(tmp_path),
            "dsn": str(live),
            "question": "West sales fell because discounting",
            "discourse": False,
        },
    )
    assert asked.get("mode") == "pearl"
    assert asked.get("overlayPromoted") is False
    assert asked.get("identify", {}).get("criterion") == "backdoor"
    assert asked.get("live", {}).get("do", {}).get("ran") is True
    rr.close()
