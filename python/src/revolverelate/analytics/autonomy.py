"""Autonomy loop on atomic relations — spec/autonomy.json.

Goal → seed chains of atoms → grammar check → dummy rollout → goal score →
select → mutate one atom at a time → record → stop → replay the winner live.

Illegal chains never reach the sandbox. Failed binds score zero and drop out.
The RNG is seeded so a run is reproducible. Never SQL from an SLM.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
from functools import lru_cache
from pathlib import Path

from revolverelate.analytics.asklog import record_ask, score_rows
from revolverelate.analytics.bind import bind_analytics_goal, list_dimensions, list_measures, resolve_column
from revolverelate.analytics.composites import check_chain, step_rank
from revolverelate.analytics.primitives import list_composites
from revolverelate.catalog import spec_dir
from revolverelate.vector.embed import fingerprint

_BIND_KEYS = ("measure", "dimension", "column", "value", "n", "threshold", "date", "year", "entity", "name", "p")


@lru_cache(maxsize=1)
def load_autonomy_spec() -> dict:
    return json.loads((spec_dir() / "autonomy.json").read_text(encoding="utf-8"))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "auto"


def chain_key(steps: list[dict]) -> int:
    return fingerprint(json.dumps(steps, sort_keys=True, default=str))


def _rank(step: dict) -> int:
    try:
        return step_rank(str(step.get("op") or ""))[0]
    except Exception:
        return 999


def _family(step: dict) -> str:
    try:
        return step_rank(str(step.get("op") or ""))[1]
    except Exception:
        return ""


def _collapse_index(steps: list[dict]) -> int | None:
    for i, step in enumerate(steps):
        if _family(step) in {"aggregate", "stat", "hierarchy"} or str(step.get("op")) == "win_share_total":
            return i
    return None


def _last_pass_start(steps: list[dict]) -> int:
    start = 0
    for i, step in enumerate(steps):
        if str(step.get("op")) == "with_cte":
            start = i + 1
    return start


def insert_sorted(steps: list[dict], new: dict) -> list[dict]:
    """Place one atom where its phase rank fits inside the last pass."""
    rank = _rank(new)
    start = _last_pass_start(steps)
    out = list(steps)
    at = len(out)
    for i in range(start, len(out)):
        if _rank(out[i]) > rank:
            at = i
            break
    out.insert(at, new)
    return out


# ---------------------------------------------------------------- goal + seeds


def bind_goal(graph, objective: str) -> dict:
    goal = bind_analytics_goal(graph, objective)
    goal["measures"] = list_measures(graph)
    goal["dimensions"] = list_dimensions(graph)
    goal["objective"] = objective
    return goal


def _slice_step(goal: dict) -> dict | None:
    sl = goal.get("slice") or {}
    if sl.get("column") and sl.get("value") is not None:
        return {"op": "eq", "column": sl["column"], "value": sl["value"]}
    return None


def seed_from_goal(goal: dict, ids: list[str]) -> list[list[dict]]:
    m, d = goal["measure"], goal["dimension"]
    sl = _slice_step(goal)
    seeds: dict[str, list[dict]] = {
        "sum_by": [{"op": "scan_fact"}, {"op": "agg_sum_by", "measure": m, "dimension": d}, {"op": "sort_value_desc"}, {"op": "limit", "n": 10}],
        "count_by": [{"op": "scan_fact"}, {"op": "agg_count_by", "dimension": d}, {"op": "sort_value_desc"}, {"op": "limit", "n": 10}],
        "share_total": [{"op": "win_share_total", "measure": m, "dimension": d}, {"op": "sort_value_desc"}, {"op": "limit", "n": 5}],
        "positive_sum_by": [
            {"op": "scan_fact"},
            {"op": "measure_positive", "measure": m},
            {"op": "agg_sum_by", "measure": m, "dimension": d},
            {"op": "sort_value_desc"},
            {"op": "limit", "n": 10},
        ],
    }
    if sl:
        seeds["slice_sum_by"] = [{"op": "scan_fact"}, sl, {"op": "agg_sum_by", "measure": m, "dimension": d}, {"op": "sort_value_desc"}, {"op": "limit", "n": 10}]
    return [seeds[i] for i in ids if i in seeds]


def _columns_exist(graph, steps: list[dict]) -> bool:
    for step in steps:
        for key in ("measure", "dimension", "column", "date"):
            name = step.get(key)
            if not name:
                continue
            try:
                resolve_column(graph, str(name))
            except Exception:
                return False
    return True


def seed_from_composites(graph) -> list[list[dict]]:
    out = []
    for comp in list_composites():
        if comp.get("sandboxOnly"):
            continue
        steps = list(comp.get("steps") or [])
        fams = {_family(s) for s in steps}
        if fams & {"vector", "chunk", "world", "search", "intent"}:
            continue
        if steps and _columns_exist(graph, steps):
            out.append(steps)
    return out


def load_memory(workdir: str | Path, rel: str) -> list[list[dict]]:
    path = Path(workdir) / rel
    if not path.exists():
        return []
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for w in (state.get("winners") or [])[:5]:
        if w.get("steps"):
            rows.append(list(w["steps"]))
    return rows


# ---------------------------------------------------------------- mutations


class Mutator:
    def __init__(self, goal: dict, graph, rng: random.Random):
        self.goal = goal
        self.graph = graph
        self.rng = rng

    def _other(self, pool: list[str], current: str | None, prefer: str | None) -> str | None:
        cands = [p for p in pool if p.casefold() != str(current or "").casefold()]
        if not cands:
            return None
        if prefer and prefer.casefold() != str(current or "").casefold() and prefer in cands and self.rng.random() < 0.6:
            return prefer
        return self.rng.choice(cands)

    def swap_measure(self, steps):
        idx = [i for i, s in enumerate(steps) if s.get("measure")]
        if not idx:
            return None
        i = self.rng.choice(idx)
        new = self._other(self.goal["measures"], steps[i]["measure"], self.goal["measure"])
        if not new:
            return None
        out = [dict(s) for s in steps]
        out[i]["measure"] = new
        return out

    def swap_dimension(self, steps):
        idx = [i for i, s in enumerate(steps) if s.get("dimension")]
        if not idx:
            return None
        i = self.rng.choice(idx)
        new = self._other(self.goal["dimensions"], steps[i]["dimension"], self.goal["dimension"])
        if not new:
            return None
        out = [dict(s) for s in steps]
        out[i]["dimension"] = new
        return out

    def _sample(self, name: str):
        try:
            bound = resolve_column(self.graph, name)
        except Exception:
            return None
        if bound.attr.samples:
            return bound.attr.samples[0]
        return None

    def add_restrict(self, steps):
        if not steps or _family(steps[0]) != "source" or str(steps[0].get("op")) == "with_cte":
            return None
        pick = self.rng.choice(["eq", "measure_positive", "gt"])
        if pick == "eq":
            dims = [d for d in self.goal["dimensions"] if self._sample(d) is not None]
            if not dims:
                return None
            d = self.rng.choice(dims)
            new = {"op": "eq", "column": d, "value": self._sample(d)}
        elif pick == "measure_positive":
            new = {"op": "measure_positive", "measure": self.goal["measure"]}
        else:
            new = {"op": "gt", "column": self.goal["measure"], "value": 0}
        if any(s == new for s in steps):
            return None
        return insert_sorted(steps, new)

    def add_cut(self, steps):
        if _collapse_index(steps) is None or any(_family(s) == "cut" for s in steps):
            return None
        pick = self.rng.choice(["top_n", "above_mean", "threshold_above"])
        m = self.goal["measure"]
        new = {"top_n": {"op": "top_n", "measure": m, "n": 5}, "above_mean": {"op": "above_mean", "measure": m}, "threshold_above": {"op": "threshold_above", "measure": m, "threshold": 0}}[pick]
        return insert_sorted(steps, new)

    def add_compare(self, steps):
        ci = _collapse_index(steps)
        if ci is None or any(_family(s) == "compare" for s in steps):
            return None
        agg = steps[ci]
        m = agg.get("measure") or self.goal["measure"]
        d = agg.get("dimension") or self.goal["dimension"]
        pick = self.rng.choice(["vs_peer", "contribution"])
        return insert_sorted(steps, {"op": pick, "measure": m, "dimension": d})

    def finish(self, steps):
        ops = [str(s.get("op")) for s in steps]
        out = list(steps)
        changed = False
        if _collapse_index(steps) is not None and "sort_value_desc" not in ops:
            out = insert_sorted(out, {"op": "sort_value_desc"})
            changed = True
        if "limit" not in ops:
            out = insert_sorted(out, {"op": "limit", "n": self.rng.choice([3, 5, 10])})
            changed = True
        return out if changed else None

    def drop_atom(self, steps):
        idx = [i for i, s in enumerate(steps) if _family(s) not in {"source", "aggregate"} and str(s.get("op")) != "win_share_total"]
        if not idx or len(steps) < 3:
            return None
        i = self.rng.choice(idx)
        return steps[:i] + steps[i + 1 :]

    def splice(self, steps, other):
        ca, cb = _collapse_index(steps), _collapse_index(other)
        if ca is None or cb is None or steps is other:
            return None
        return list(steps[:ca]) + list(other[cb:])

    def propose(self, parents: list[list[dict]], mutation_ids: list[str]) -> list[list[dict]]:
        out = []
        for parent in parents:
            for mid in mutation_ids:
                child = None
                if mid == "splice":
                    others = [p for p in parents if p is not parent]
                    if others:
                        child = self.splice(parent, self.rng.choice(others))
                else:
                    fn = getattr(self, mid, None)
                    if fn is not None:
                        child = fn(parent)
                if child and child != parent and len(child) <= int(load_autonomy_spec()["budget"]["maxDepth"]):
                    out.append(child)
        return out


# ---------------------------------------------------------------- score


def score_candidate(goal: dict, spec: dict, *, legal: bool, ran: bool, row_count: int, rows: list, steps: list[dict], novel: bool) -> dict:
    w = spec["score"]["weights"]
    lo, hi = spec["score"]["rowBand"]
    parts = {"legal": 1.0 if legal else 0.0, "ran": 1.0 if ran else 0.0}
    parts["rows"] = 1.0 if ran and lo <= row_count <= hi else (0.5 if ran and row_count > 0 else 0.0)
    gm, gd = str(goal["measure"]).casefold(), str(goal["dimension"]).casefold()
    parts["goalMeasure"] = 1.0 if any(str(s.get("measure", "")).casefold() == gm for s in steps) else 0.0
    parts["goalDimension"] = 1.0 if any(str(s.get("dimension", "")).casefold() == gd for s in steps) else 0.0
    sl = goal.get("slice") or {}
    if sl.get("column"):
        parts["goalSlice"] = 1.0 if any(str(s.get("column", "")).casefold() == str(sl["column"]).casefold() and str(s.get("value")) == str(sl.get("value")) for s in steps) else 0.0
    else:
        parts["goalSlice"] = 1.0
    mag = abs(score_rows(rows)) if ran else 0.0
    parts["magnitude"] = min(1.0, math.log1p(mag) / math.log1p(float(spec["score"]["magnitudeLog"]))) if mag > 0 else 0.0
    parts["novelty"] = 1.0 if novel else 0.0
    parts["depth"] = len(steps) / float(spec["budget"]["maxDepth"])
    total = sum(float(w.get(k, 0.0)) * v for k, v in parts.items())
    return {"total": round(total, 4), "parts": {k: round(v, 4) for k, v in parts.items()}}


# ---------------------------------------------------------------- runner


def _evaluate(rr, goal: dict, spec: dict, steps: list[dict], *, gen: int, seen: set[int]) -> dict:
    key = chain_key(steps)
    novel = key not in seen
    seen.add(key)
    report = check_chain(steps)
    row: dict = {
        "gen": gen,
        "key": key,
        "steps": steps,
        "ops": [str(s.get("op")) for s in steps],
        "legal": bool(report["ok"]),
        "issues": report["issues"],
        "ran": False,
        "rowCount": 0,
        "planId": None,
        "error": None,
    }
    rows: list = []
    if report["ok"]:
        plan_id = _slug(f"{spec['record']['plansPrefix']}-{key}")
        try:
            plan = rr.analytics.run_chain(steps, plan_id=plan_id)
            rows = plan.get("rows") or []
            row.update({"ran": plan.get("status") == "sandbox_ok", "rowCount": int(plan.get("rowCount") or 0), "planId": plan.get("id"), "columns": plan.get("columns")})
            row["sample"] = rows[:5]
        except Exception as exc:
            row["error"] = str(exc)[:200]
    row["score"] = score_candidate(goal, spec, legal=row["legal"], ran=row["ran"], row_count=row["rowCount"], rows=rows, steps=steps, novel=novel)
    if spec["record"].get("askLog") and row["legal"]:
        try:
            record_ask(
                rr.sandbox,
                question=" > ".join(row["ops"]),
                objective=goal["objective"],
                ir=None,
                status="sandbox_ok" if row["ran"] else "bind_failed",
                composite=row["planId"] or "",
                pattern=str(spec["record"]["pattern"]),
                score=row["score"]["total"],
                row_count=row["rowCount"],
            )
        except Exception:
            pass
    return row


def _save_state(workdir: str | Path, rel: str, state: dict) -> None:
    path = Path(workdir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def run_autonomy(
    rr,
    objective: str,
    *,
    generations: int | None = None,
    population: int | None = None,
    live: bool = True,
    seed: int = 7,
) -> dict:
    spec = load_autonomy_spec()
    budget = spec["budget"]
    gens = min(int(generations or budget["generations"]), int(budget["hardMaxGenerations"]))
    pop = int(population or budget["population"])
    keep = int(budget["keep"])
    patience = int(budget["patience"])
    min_gens = int(budget["minGenerations"])
    rng = random.Random(seed)

    graph = rr.schema
    goal = bind_goal(graph, objective)
    mut = Mutator(goal, graph, rng)
    seeds_cfg = spec["seeds"]

    pool: list[list[dict]] = []
    pool += seed_from_goal(goal, list(seeds_cfg.get("fromGoal") or []))
    memory = load_memory(rr.workdir, seeds_cfg["memoryFile"]) if seeds_cfg.get("fromMemory") else []
    pool += memory
    if seeds_cfg.get("fromComposites"):
        pool += seed_from_composites(graph)

    seen: set[int] = set()
    dedup: list[list[dict]] = []
    for steps in pool:
        k = chain_key(steps)
        if k not in {chain_key(s) for s in dedup}:
            dedup.append(steps)
    candidates = dedup[:pop]

    history: list[dict] = []
    elite: list[dict] = []
    best = -1.0
    stale = 0
    stop = "generations"
    mutation_ids = [m["id"] for m in spec["mutations"]]

    for gen in range(1, gens + 1):
        evaluated = [_evaluate(rr, goal, spec, steps, gen=gen, seen=seen) for steps in candidates]
        history.extend(evaluated)
        merged = elite + evaluated
        merged.sort(key=lambda r: (r["score"]["total"], r["ran"], -len(r["steps"])), reverse=True)
        uniq: list[dict] = []
        for r in merged:
            if r["key"] not in {u["key"] for u in uniq}:
                uniq.append(r)
        elite = uniq[:keep]
        top = elite[0]["score"]["total"] if elite else -1.0
        if top > best + 1e-9:
            best, stale = top, 0
        else:
            stale += 1
        if gen >= min_gens:
            if best >= float(spec["stop"]["target"]):
                stop = "target"
                break
            if stale >= patience:
                stop = "patience"
                break
        if gen == gens:
            break
        parents = [r["steps"] for r in elite if r["legal"]] or [r["steps"] for r in elite]
        proposed = mut.propose(parents, mutation_ids)
        rng.shuffle(proposed)
        fresh: list[list[dict]] = []
        for steps in proposed:
            k = chain_key(steps)
            if k in seen or k in {chain_key(s) for s in fresh}:
                continue
            fresh.append(steps)
            if len(fresh) >= pop:
                break
        if not fresh:
            stop = "exhausted"
            break
        candidates = fresh

    winner = next((r for r in elite if r["ran"]), elite[0] if elite else None)
    live_out = None
    if live and winner and winner.get("planId"):
        live_out = rr.replay_live(plan_id=winner["planId"])

    winners = [{"steps": r["steps"], "ops": r["ops"], "score": r["score"]["total"], "planId": r["planId"], "ran": r["ran"]} for r in elite]
    state = {
        "objective": objective,
        "goal": {k: goal[k] for k in ("measure", "dimension", "column", "slice")},
        "generations": history[-1]["gen"] if history else 0,
        "evaluated": len(history),
        "legal": sum(1 for r in history if r["legal"]),
        "ran": sum(1 for r in history if r["ran"]),
        "illegalNeverRan": sum(1 for r in history if not r["legal"]),
        "stop": stop,
        "best": best,
        "winner": winner,
        "winners": winners,
        "memorySeeds": len(memory),
        "live": live_out,
        "history": [{k: r[k] for k in ("gen", "ops", "legal", "ran", "rowCount", "planId", "error")} | {"score": r["score"]["total"]} for r in history],
        "honesty": spec["honesty"],
        "identification": "none",
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save_state(rr.workdir, seeds_cfg["memoryFile"], state)
    return state
