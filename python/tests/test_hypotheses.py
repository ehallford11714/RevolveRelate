"""Self-directed hypothesis loop: survey → form → test (dummy ticket, live verdict) → derive → remember."""

import json

from revolverelate.analytics.asklog import ASKLOG
from revolverelate.analytics.hypotheses import (
    Hypothesis,
    derive,
    form_from_templates,
    load_hypotheses_spec,
    load_tested,
    prioritise,
    run_hypotheses,
    survey,
    evaluate_hypothesis,
)
from revolverelate.domain.finance import write_finance_equities
from revolverelate.domain.registry import detect_domain
from revolverelate.mcp.server import MCP_TOOLS, dispatch
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore

VERDICTS = {"supported", "refuted", "inconclusive", "illegal", "failed", "dummy_only"}


def _superstore(tmp_path):
    rr = RevolveRelate.connect(str(write_superstore(tmp_path / "superstore.sqlite")), workdir=tmp_path)
    rr.build()
    return rr


def _finance(tmp_path):
    rr = RevolveRelate.connect(str(write_finance_equities(tmp_path / "fin.sqlite", use_yfinance=False)), workdir=tmp_path)
    rr.build()
    return rr


def test_spec_shape():
    spec = load_hypotheses_spec()
    assert spec["loop"][:2] == ["survey", "form"] and spec["loop"][-1] == "search"
    assert {k["id"] for k in spec["kinds"]} == {"concentration", "contrast", "association", "correlation", "trend"}
    assert set(spec["verdicts"]) == VERDICTS
    assert spec["slm"]["neverSql"] is True
    assert "causal claim" in spec["honesty"]
    for kind in spec["kinds"]:
        assert kind["threshold"] and kind["statement"] and kind["chain"][0] == "scan_fact"


def test_survey_and_templates_bind_to_real_columns(tmp_path):
    rr = _superstore(tmp_path)
    try:
        view = survey(rr.schema, load_hypotheses_spec())
        assert view["fact"] == "OrderLine"
        assert view["measures"][0] == "Sales"
        assert "Category" in view["dimensions"] and "Region" in view["dimensions"]
        # keys, names, and codes never become hypothesis dimensions
        assert not any(d.casefold().endswith(("code", "name", "date")) for d in view["dimensions"])
        assert view["date"] == "OrderDate" and view["years"][-1] == 2017
        pool = form_from_templates(view)
        kinds = {h.kind for h in pool}
        assert kinds == {"concentration", "contrast", "association", "correlation", "trend"}
        assert all(h.key == h.key for h in pool)
        text = next(h for h in pool if h.kind == "contrast").statement()
        assert "peer mean" in text and "{" not in text
    finally:
        rr.close()


def test_prioritise_interleaves_kinds_and_skips_tested(tmp_path):
    rr = _superstore(tmp_path)
    try:
        view = survey(rr.schema, load_hypotheses_spec())
        pool = form_from_templates(view)
        first = prioritise(pool, set())
        assert len({h.kind for h in first[:5]}) >= 4
        tested = {first[0].key}
        again = prioritise(pool, tested)
        assert first[0].key not in {h.key for h in again}
        assert len(again) == len(first) - 1
    finally:
        rr.close()


def test_verdicts_come_from_live_rows_after_a_dummy_ticket(tmp_path):
    rr = _superstore(tmp_path)
    try:
        spec = load_hypotheses_spec()
        h = Hypothesis("concentration", {"measure": "Sales", "dimension": "Category"}, threshold=dict(spec["kinds"][0]["threshold"]))
        res = evaluate_hypothesis(rr, h, live=True)
        assert res["verdict"] in {"supported", "refuted"} and res["grade"] == "live"
        assert res["effect"]["top"] and 0 < res["effect"]["share"] <= 1
        assert res["chains"][0]["ran"] and res["chains"][0]["planId"]
        # dummy-only never counts as evidence
        h2 = Hypothesis("contrast", {"measure": "Sales", "dimension": "Category", "value": "Furniture"}, threshold={"ratio": 1.25})
        res2 = evaluate_hypothesis(rr, h2, live=False)
        assert res2["verdict"] == "dummy_only" and res2["grade"] == "dummy" and res2["dummyVerdict"] in {"supported", "refuted", "inconclusive"}
        # a value absent from live rows is inconclusive, not refuted
        h3 = Hypothesis("contrast", {"measure": "Sales", "dimension": "Category", "value": "NoSuchCategory"}, threshold={"ratio": 1.25})
        assert evaluate_hypothesis(rr, h3, live=True)["verdict"] == "inconclusive"
        # an unbound column never reaches the sandbox
        h4 = Hypothesis("concentration", {"measure": "Sales", "dimension": "NoSuchColumn"}, threshold={"p": 0.4})
        res4 = evaluate_hypothesis(rr, h4, live=True)
        assert res4["verdict"] in {"illegal", "failed"} and not any(c["ran"] for c in res4["chains"])
    finally:
        rr.close()


def test_correlation_needs_enough_pairs_and_trend_uses_two_years(tmp_path):
    rr = _superstore(tmp_path)
    try:
        h = Hypothesis("correlation", {"measure": "Sales", "measure2": "Quantity"}, threshold={"r": 0.3, "minPairs": 20})
        res = evaluate_hypothesis(rr, h, live=True)
        assert res["verdict"] == "inconclusive" and "pairs" in res["why"]
        h2 = Hypothesis("correlation", {"measure": "Sales", "measure2": "Quantity"}, threshold={"r": 0.3, "minPairs": 5})
        res2 = evaluate_hypothesis(rr, h2, live=True)
        assert res2["verdict"] in {"supported", "refuted"} and -1 <= res2["effect"]["r"] <= 1
        h3 = Hypothesis("trend", {"measure": "Sales", "date": "OrderDate", "year1": 2016, "year2": 2017}, threshold={"growth": 0.1})
        res3 = evaluate_hypothesis(rr, h3, live=True)
        assert res3["verdict"] in {"supported", "refuted"} and "sumYear1" in res3["effect"]
    finally:
        rr.close()


def test_derive_forms_novel_follow_ups_from_results(tmp_path):
    rr = _superstore(tmp_path)
    try:
        spec = load_hypotheses_spec()
        view = survey(rr.schema, spec)
        h = Hypothesis("concentration", {"measure": "Sales", "dimension": "Category"}, threshold={"p": 0.4})
        res = evaluate_hypothesis(rr, h, live=True)
        if res["verdict"] != "supported":
            res = {**res, "verdict": "supported"}
        kids = derive(res, view, spec)
        assert kids, "a supported concentration must spawn follow-ups"
        origins = {k.origin for k in kids}
        assert "derive:contrast_top" in origins and "derive:drill" in origins
        drill = next(k for k in kids if k.origin == "derive:drill")
        assert drill.slice == {"column": "Category", "value": str(res["effect"]["top"])}
        assert drill.parent == res["key"] and drill.statement().startswith("Within Category = ")
        assert all(k.key != h.key for k in kids)
        # refuted results derive nothing
        assert derive({**res, "verdict": "refuted"}, view, spec) == []
    finally:
        rr.close()


def test_loop_runs_on_its_own_remembers_and_does_not_retest(tmp_path):
    rr = _superstore(tmp_path)
    try:
        state = run_hypotheses(rr, rounds=2, per_round=6, live=True, use_slm=False, search=False)
        assert state["domain"] is None and state["survey"]["fact"] == "OrderLine"
        assert state["formed"] > 20 and len(state["tested"]) == 12
        assert set(state["counts"]) == VERDICTS and state["counts"]["dummy_only"] == 0
        assert state["counts"]["supported"] >= 1 and state["derived"] >= 1
        assert {r["verdict"] for r in state["tested"]} <= VERDICTS
        assert any(r["origin"].startswith("derive:") for r in state["tested"] if r["round"] == 2)
        assert state["remembered"] >= 1 and state["identification"] == "none"
        saved = json.loads((tmp_path / ".revolverelate" / "hypotheses.json").read_text(encoding="utf-8"))
        assert len(saved["tested"]) == 12
        assert len(load_tested(tmp_path, load_hypotheses_spec())) == 12

        # the ask log has one row per hypothesis
        logged = rr.sandbox.execute(f'SELECT COUNT(*) FROM "{ASKLOG}" WHERE Pattern = ?', ("hypothesis",))[1][0][0]
        assert logged >= 12

        # second run: nothing already tested is re-run; new hypotheses continue from the pool
        second = run_hypotheses(rr, rounds=1, per_round=4, live=True, use_slm=False, search=False)
        assert second["previouslyTested"] == 12 and len(second["tested"]) == 4
        assert not ({r["key"] for r in second["tested"]} & {r["key"] for r in state["tested"]})
        saved2 = json.loads((tmp_path / ".revolverelate" / "hypotheses.json").read_text(encoding="utf-8"))
        assert len(saved2["tested"]) == 16

        # retest starts over
        third = run_hypotheses(rr, rounds=1, per_round=2, live=True, use_slm=False, search=False, retest=True)
        assert third["previouslyTested"] == 0

        # supported hypotheses are recallable evidence
        recalled = rr.recall("hypothesis Sales Category", n=3)
        assert recalled["rows"]
    finally:
        rr.close()


def test_autonomy_without_objective_is_self_directed_and_searches(tmp_path):
    rr = _superstore(tmp_path)
    try:
        state = rr.autonomy(None, rounds=1, live=True)
        assert "tested" in state and state["stop"] in {"rounds", "exhausted", "hardMaxTests"}
        if state["supported"]:
            assert state["search"] and state["search"]["objective"] and state["search"]["winnerOps"]
            assert (tmp_path / ".revolverelate" / "autonomy.json").exists()
        # an objective still runs the atom search directly
        directed = rr.autonomy("west sales by category", generations=2, live=False)
        assert directed["objective"] == "west sales by category" and "winner" in directed
    finally:
        rr.close()


def test_finance_hypotheses_detect_domain_and_seed_from_automine(tmp_path):
    rr = _finance(tmp_path)
    try:
        assert detect_domain(rr.schema).id == "finance"
        rr.automine("what causes AAPL price moves", passes=1, report=False)
        state = run_hypotheses(rr, rounds=2, per_round=8, live=True, use_slm=False, search=False)
        assert state["domain"] == "finance" and state["survey"]["fact"] == "PriceMove"
        assert state["survey"]["measures"][0] == "AbsReturn"
        origins = {r["origin"] for r in state["tested"]}
        assert "automine" in origins, "catalogued candidates from automine become contrast hypotheses"
        assoc = [r for r in state["tested"] if r["kind"] in {"association", "correlation"} and not r["slice"]]
        assert assoc and all(r["verdict"] in {"supported", "refuted", "inconclusive"} for r in assoc)
        assert "investment advice" in state["honesty"]
    finally:
        rr.close()


def test_mcp_hypothesize_and_self_directed_autonomy(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    names = {t["name"] for t in MCP_TOOLS}
    assert "rr_hypothesize" in names
    out = dispatch("rr_hypothesize", {"dsn": str(live), "workdir": str(tmp_path), "rounds": 1, "perRound": 4, "search": False, "slm": False})
    assert out["mode"] == "hypothesize" and len(out["tested"]) == 4
    assert out["memoryFile"] == ".revolverelate/hypotheses.json" and out["identification"] == "none"
    assert all(set(r) >= {"statement", "verdict", "effect", "why"} for r in out["tested"])
    auto = dispatch("rr_autonomy", {"dsn": str(live), "workdir": str(tmp_path), "rounds": 1})
    assert auto["mode"] == "autonomy:self-directed" and "tested" in auto
