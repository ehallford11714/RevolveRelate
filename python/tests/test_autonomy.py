"""Autonomy loop on atomic relations: grammar first, dummy second, live last."""

import json

from revolverelate.analytics.asklog import ASKLOG
from revolverelate.analytics.autonomy import (
    Mutator,
    bind_goal,
    insert_sorted,
    load_autonomy_spec,
    run_autonomy,
    score_candidate,
    seed_from_goal,
)
from revolverelate.analytics.composites import check_chain
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore


def _boot(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    return rr


def test_spec_shape():
    spec = load_autonomy_spec()
    assert spec["unit"].startswith("one analytics primitive")
    assert spec["loop"][:3] == ["goal", "seed", "propose"] and spec["loop"][-1] == "replay"
    assert spec["slm"]["neverSql"] is True and spec["slm"]["neverNewAtoms"] is True
    assert {m["id"] for m in spec["mutations"]} >= {"swap_measure", "add_restrict", "add_cut", "splice", "drop_atom"}
    assert spec["score"]["weights"]["depth"] < 0
    assert "heuristic" in spec["honesty"]


def test_seeds_are_legal_and_bound_to_goal(tmp_path):
    rr = _boot(tmp_path)
    goal = bind_goal(rr.schema, "west sales by category")
    assert goal["measure"] == "Sales" and goal["dimension"] == "Category"
    assert goal["slice"] == {"column": "Region", "value": "West"}
    seeds = seed_from_goal(goal, load_autonomy_spec()["seeds"]["fromGoal"])
    assert len(seeds) == 5
    for steps in seeds:
        assert check_chain(steps)["ok"], steps
    rr.close()


def test_insert_sorted_keeps_phase_forward():
    base = [{"op": "scan_fact"}, {"op": "agg_sum_by", "measure": "Sales", "dimension": "Category"}, {"op": "limit", "n": 5}]
    out = insert_sorted(base, {"op": "eq", "column": "Region", "value": "West"})
    assert [s["op"] for s in out] == ["scan_fact", "eq", "agg_sum_by", "limit"]
    out = insert_sorted(base, {"op": "top_n", "measure": "Sales", "n": 3})
    assert [s["op"] for s in out] == ["scan_fact", "agg_sum_by", "top_n", "limit"]
    assert check_chain(out)["ok"]


def test_mutations_stay_inside_grammar(tmp_path):
    import random

    rr = _boot(tmp_path)
    goal = bind_goal(rr.schema, "west sales by category")
    mut = Mutator(goal, rr.schema, random.Random(1))
    parents = seed_from_goal(goal, ["sum_by", "slice_sum_by"])
    children = mut.propose(parents, [m["id"] for m in load_autonomy_spec()["mutations"]])
    assert children
    for child in children:
        assert child not in parents
        assert all(isinstance(s.get("op"), str) for s in child)
    legal = [c for c in children if check_chain(c)["ok"]]
    assert len(legal) >= len(children) // 2
    rr.close()


def test_illegal_chain_scores_zero_without_running(tmp_path):
    rr = _boot(tmp_path)
    goal = bind_goal(rr.schema, "sales by category")
    spec = load_autonomy_spec()
    bad = [{"op": "limit", "n": 5}, {"op": "agg_sum_by", "measure": "Sales", "dimension": "Category"}]
    assert not check_chain(bad)["ok"]
    score = score_candidate(goal, spec, legal=False, ran=False, row_count=0, rows=[], steps=bad, novel=True)
    assert score["parts"]["legal"] == 0.0 and score["parts"]["ran"] == 0.0
    good = score_candidate(goal, spec, legal=True, ran=True, row_count=3, rows=[["a", 100.0]], steps=bad, novel=True)
    assert good["total"] > score["total"]
    rr.close()


def test_loop_runs_dummy_first_then_live_and_remembers(tmp_path):
    rr = _boot(tmp_path)
    state = run_autonomy(rr, "west sales by category", generations=3, population=4)
    assert state["identification"] == "none"
    assert state["evaluated"] >= 4
    assert state["legal"] == state["evaluated"] - state["illegalNeverRan"]
    assert state["ran"] >= 1
    winner = state["winner"]
    assert winner["ran"] and winner["planId"]
    assert set(winner["ops"]) & {"agg_sum_by", "win_share_total"}
    assert any(s.get("column") == "Region" and s.get("value") == "West" for s in winner["steps"])
    assert any(s.get("measure") == "Sales" and s.get("dimension") == "Category" for s in winner["steps"])
    plan = rr.analytics.load(winner["planId"])
    assert plan["status"] in {"sandbox_ok", "promoted"}
    assert state["live"]["ran"] is True and state["live"]["target"] == "live"
    assert "UPDATE" not in (state["live"]["sql"] or "").upper()
    for row in state["history"]:
        if not row["legal"]:
            assert not row["ran"] and row["planId"] is None

    saved = json.loads((tmp_path / ".revolverelate" / "autonomy.json").read_text(encoding="utf-8"))
    assert saved["winners"] and saved["honesty"]
    logged = rr.sandbox.execute(f'SELECT COUNT(*) FROM "{ASKLOG}" WHERE Pattern = ?', ("autonomy",))[1][0][0]
    assert logged >= state["legal"]

    again = run_autonomy(rr, "loss by segment", generations=2, population=4, live=False)
    assert again["memorySeeds"] >= 1
    assert again["live"] is None
    rr.close()


def test_api_and_mcp_expose_autonomy(tmp_path):
    from revolverelate.mcp.server import MCP_TOOLS, dispatch

    assert "rr_autonomy" in {t["name"] for t in MCP_TOOLS}
    live = write_superstore(tmp_path / "superstore.sqlite")
    out = dispatch("rr_autonomy", {"dsn": str(live), "workdir": str(tmp_path), "objective": "sales by category", "generations": 2, "population": 3, "live": False})
    assert out.get("mode") == "autonomy"
    assert out["winner"]["ops"] and out["identification"] == "none"
    # no objective is no longer an error: the engine forms and tests its own hypotheses
    self_directed = dispatch("rr_autonomy", {"dsn": str(live), "workdir": str(tmp_path), "rounds": 1, "live": False})
    assert self_directed.get("mode") == "autonomy:self-directed" and "tested" in self_directed
