"""Because-clause bind, heuristic search, GLM odds-ratio. No Chroma."""

from __future__ import annotations

import math

from revolverelate.analytics.composites import check_chain
from revolverelate.analytics.heuristic import bind_because, glm_odds_ratio
from revolverelate.analytics.primitives import get_composite
from revolverelate.mcp.server import dispatch
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore


def test_glm_odds_ratio_recovers_a_known_cause():
    # 2×2: high treatment strongly tied to the outcome (OR ≈ 16).
    y = [1] * 20 + [1] * 5 + [0] * 5 + [0] * 20
    d = [1] * 20 + [0] * 5 + [1] * 5 + [0] * 20
    got = glm_odds_ratio(y, d)
    assert got["n"] == 50
    assert got["oddsRatio"] > 8
    assert got["pValue"] < 0.001
    assert got["logOdds"] > 0


def test_glm_odds_ratio_rejects_independence():
    y = [1, 0, 1, 0] * 10
    d = [1, 1, 0, 0] * 10
    got = glm_odds_ratio(y, d)
    assert got["pValue"] > 0.2
    assert abs(math.log(got["oddsRatio"])) < 0.5


def test_because_bind_maps_discount_sales_west(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=6)
    bind = bind_because(rr.schema, "why did West sales fall because discounting was heavy")
    assert bind["treatment"] == "Discount"
    assert bind["outcome"] == "Sales"
    assert bind["slice"]["column"] == "Region"
    assert bind["slice"]["value"] == "West"
    assert bind["outcomeEncode"] == "low"
    assert bind["treatmentEncode"] == "high"
    rr.close()


def test_heuristic_west_facts_is_live_promoteable():
    spec = get_composite("heuristic_west_facts")
    assert spec.get("sandboxOnly") is not True
    assert check_chain(spec["steps"])["ok"]
    assert not any(s.get("op") in {"overlay", "knn", "chunk_causal"} for s in spec["steps"])


def test_heuristic_cause_finds_supported_edge_and_promotes_facts(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=8)
    out = rr.heuristic_cause(
        "why did West sales fall because discounting",
        live=True,
        discourse=False,
    )
    assert out["kind"] == "causal_heuristic"
    assert out["identification"] == "none"
    assert out["overlayPromoted"] is False
    assert out["bind"]["treatment"] == "Discount"
    assert out["bind"]["outcome"] == "Sales"
    assert out["sandbox"]["status"] == "sandbox_ok"
    assert (out["sandbox"]["rowCount"] or 0) >= 1
    winner = out["winner"]
    assert winner["treatment"]
    assert winner["oddsRatio"] is not None
    supported = [c for c in out["candidates"] if c.get("supported")]
    assert supported, out["candidates"]
    assert any(c["treatment"] == "Discount" and c["outcome"] == "Sales" for c in supported)
    hyp = out["hypothesis"]
    assert hyp["treatment"] == "Discount"
    assert hyp["outcome"] == "Sales"
    assert out["live"]["ran"] is True
    assert (out["live"].get("rowCount") or 0) >= 1
    asked = dispatch(
        "rr_causal_heuristic",
        {
            "workdir": str(tmp_path),
            "dsn": str(live),
            "question": "West sales fell because discounting",
            "discourse": False,
        },
    )
    assert asked.get("mode") == "causal_heuristic"
    assert asked.get("overlayPromoted") is False
    rr.close()
