"""Composite grammar: phase order, depth, deep named chains."""

from __future__ import annotations

import pytest

from revolverelate.analytics.composites import assert_chain, check_chain, load_composite_rules
from revolverelate.analytics.primitives import get_composite, list_composites, list_families
from revolverelate.errors import AskError
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore


@pytest.fixture
def rr(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    inst = RevolveRelate.connect(str(live), workdir=tmp_path)
    inst.build(rows_per_entity=8)
    yield inst
    inst.close()


def test_rules_cover_every_family():
    rules = load_composite_rules()
    covered = {f for p in rules["phases"] for f in p["families"]}
    assert {f["id"] for f in list_families()} <= covered
    assert rules["depth"]["hardMax"] == 24
    assert rules["depth"]["deep"] == 12


def test_typical_chain_is_legal():
    report = check_chain(
        [
            {"op": "scan_fact"},
            {"op": "eq", "column": "Region", "value": "West"},
            {"op": "agg_sum_by", "measure": "Sales", "dimension": "Category"},
            {"op": "sort_value_desc"},
            {"op": "limit", "n": 10},
        ]
    )
    assert report["ok"]
    assert report["typical"]
    assert not report["deep"]
    assert report["passes"] == 1


def test_backward_and_second_collapse_are_illegal():
    back = check_chain([{"op": "limit", "n": 5}, {"op": "agg_sum_by"}])
    assert not back["ok"]
    double = check_chain(
        [
            {"op": "scan_fact"},
            {"op": "agg_sum_by"},
            {"op": "agg_avg_by"},
        ]
    )
    assert not double["ok"]
    with pytest.raises(AskError, match="Illegal composite"):
        assert_chain([{"op": "limit", "n": 1}, {"op": "scan_fact"}])


def test_with_cte_starts_a_second_pass():
    report = check_chain(
        [
            {"op": "scan_fact"},
            {"op": "agg_sum_by", "measure": "Sales", "dimension": "Region"},
            {"op": "with_cte", "name": "book"},
            {"op": "vs_peer", "measure": "Sales", "dimension": "Region"},
            {"op": "above_mean"},
            {"op": "sort_value_desc"},
        ]
    )
    assert report["ok"], report["issues"]
    assert report["passes"] >= 2


def test_named_composites_obey_grammar():
    for row in list_composites():
        report = check_chain(row["steps"])
        assert report["ok"], (row["id"], report["issues"])
        assert report["depth"] <= report["hardMax"]


def test_deep_compare_cut_is_deep_and_runs(rr):
    row = get_composite("deep_compare_cut")
    assert len(row["steps"]) >= 8
    report = check_chain(row["steps"])
    assert report["ok"], report["issues"]
    plan = rr.analytics.run_chain(composite="deep_compare_cut")
    assert plan["status"] == "sandbox_ok"
    assert plan["chainCheck"]["ok"]
    live = rr.analytics.promote(plan["id"])
    assert live["status"] == "promoted"


def test_new_family_composites_roll_out(rr):
    for cid in ("west_growth_by_category", "latest_then_margin", "quality_then_book", "two_pass_peer_cut"):
        plan = rr.analytics.run_chain(composite=cid)
        assert plan["status"] == "sandbox_ok", cid
        assert plan["chainCheck"]["ok"], cid
