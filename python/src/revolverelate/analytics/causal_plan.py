"""CausalPlan: SLM proposes primitive chains. Never SQL."""

from __future__ import annotations

import json
import re

from revolverelate.analytics.composites import check_chain
from revolverelate.analytics.primitives import get_composite, primitive_ids
from revolverelate.catalog import spec_dir


def load_causal_plan_spec() -> dict:
    return json.loads((spec_dir() / "causal-plan.json").read_text(encoding="utf-8"))


def allowed_ops() -> set[str]:
    listed = load_causal_plan_spec().get("allowedOps") or []
    known = set(primitive_ids())
    return {str(x) for x in listed if str(x) in known}


def match_causal_composite(question: str) -> str:
    text = (question or "").casefold()
    fallback = load_causal_plan_spec().get("fallback") or []
    for row in fallback:
        tokens = [str(t).casefold() for t in row.get("match") or []]
        if not tokens:
            return str(row.get("composite") or "rag_causal_knn")
        if any(re.search(rf"\b{re.escape(token)}\b", text) for token in tokens):
            return str(row.get("composite") or "rag_causal_knn")
    return "rag_causal_knn"


def _clean_steps(raw) -> list[dict]:
    allow = allowed_ops()
    steps: list[dict] = []
    if not isinstance(raw, list):
        return steps
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("op") or item.get("primitive") or item.get("id") or "")
        if pid not in allow:
            continue
        step = {k: v for k, v in item.items() if k != "primitive"}
        step["op"] = pid
        steps.append(step)
    return steps


def normalize_causal_plan(data: dict | None, question: str, graph=None) -> dict:
    """Coerce SLM JSON into a CausalPlan. Grammar must still pass."""
    from revolverelate.analytics.bind import bind_analytics_goal

    blob = dict(data or {})
    query = str(blob.get("query") or blob.get("question") or question or "").strip()
    cid = str(blob.get("composite") or "").strip()
    bound = bind_analytics_goal(graph, query or question) if graph is not None else {}
    steps = _clean_steps(blob.get("steps"))
    if not steps:
        cid = cid or match_causal_composite(query or question)
        spec = get_composite(cid)
        steps = list(spec.get("steps") or [])
    goal_in = blob.get("goal") if isinstance(blob.get("goal"), dict) else {}
    goal = {
        "measure": goal_in.get("measure") or bound.get("measure") or "Sales",
        "dimension": goal_in.get("dimension") or bound.get("dimension") or "Category",
        "slice": goal_in.get("slice") if goal_in.get("slice") is not None else bound.get("slice") or {},
        "column": goal_in.get("column") or bound.get("column") or "ProductName",
        "treatment": goal_in.get("treatment") or bound.get("treatment"),
    }
    steps = bind_causal_steps(steps, query=query or question, column=str(goal["column"]), n=8, goal=goal)
    report = check_chain(steps)
    if not report.get("ok"):
        cid = match_causal_composite(query or question)
        spec = get_composite(cid)
        steps = bind_causal_steps(list(spec.get("steps") or []), query=query or question, column=str(goal["column"]), n=8, goal=goal)
        report = check_chain(steps)
    if not cid:
        cid = match_causal_composite(query or question)
    return {
        "kind": "causal_plan",
        "goal": goal,
        "query": query or question or "what causes this",
        "composite": cid,
        "steps": steps,
        "grammar": {k: report.get(k) for k in ("ok", "depth", "issues", "ranks")},
        "sandboxOnly": False,
    }


def fallback_causal_plan(question: str, graph=None) -> dict:
    return normalize_causal_plan({"query": question}, question, graph)


def causal_candidates() -> list[str]:
    spec = load_causal_plan_spec().get("abduce") or {}
    listed = [str(x) for x in spec.get("candidates") or []]
    return listed or ["rag_causal_knn", "rag_causal_pair", "causal_then_agg", "causal_then_intervene"]


def _abduce_score_spec() -> dict:
    spec = load_causal_plan_spec().get("abduce") or {}
    raw = spec.get("score") if isinstance(spec.get("score"), dict) else {}
    return {
        "worldScale": float(raw.get("worldScale") or 100),
        "hintBonus": float(raw.get("hintBonus") or 2),
        "memoryBonus": float(raw.get("memoryBonus") or 1),
        "goalBonus": float(raw.get("goalBonus") or 0.5),
    }


def bind_causal_steps(
    steps: list[dict],
    *,
    query: str,
    column: str = "ProductName",
    n: int = 8,
    goal: dict | None = None,
) -> list[dict]:
    """Copy a named composite's steps and bind knn/query + goal grain. Never SQL."""
    goal = goal if isinstance(goal, dict) else {}
    measure = str(goal.get("measure") or "Sales")
    dimension = str(goal.get("dimension") or "Category")
    treatment = str(goal.get("treatment") or measure)
    text_col = str(goal.get("column") or column or "ProductName")
    slice_ = goal.get("slice") if isinstance(goal.get("slice"), dict) else {}
    out: list[dict] = []
    for raw in steps or []:
        step = dict(raw)
        op = str(step.get("op") or "")
        if op in {"overlay", "chunk_semantic", "chunk_causal", "chunk_topic", "chunk_event", "sim_join", "knn"}:
            step["column"] = text_col
        if op == "knn":
            step["query"] = query
            step["n"] = n
            step["k"] = n
        if op in {"agg_sum_by", "vs_peer", "goal"}:
            step["measure"] = measure
            step["dimension"] = dimension
        if op in {"intervene", "vs_world"}:
            step["measure"] = treatment
            if slice_.get("column"):
                step["column"] = slice_["column"]
                step["value"] = slice_.get("value")
        if op == "attach_fact":
            if slice_.get("column"):
                step["column"] = slice_["column"]
                step["value"] = slice_.get("value")
            else:
                step.pop("column", None)
                step.pop("value", None)
        if op == "eq" and slice_.get("column"):
            step["column"] = slice_["column"]
            step["value"] = slice_.get("value")
        out.append(step)
    return out


def score_causal_rows(columns, rows, *, world_scale: float = 100.0) -> float:
    """Goal utility on dummy rows. vs_world uses |observed-intervened|; else evidence count."""
    cols = [str(c).casefold() for c in columns or []]
    data = list(rows or [])
    if "observed" in cols and "intervened" in cols:
        oi, ii = cols.index("observed"), cols.index("intervened")
        delta = 0.0
        for row in data:
            try:
                delta += abs(float(row[oi] or 0) - float(row[ii] or 0))
            except (TypeError, ValueError, IndexError):
                continue
        return delta * float(world_scale) + float(len(data))
    return float(len(data))


def _row_get(row, idx: dict[str, int], name: str, default=""):
    pos = idx.get(name)
    if pos is None or pos < 0 or pos >= len(row):
        return default
    return row[pos]


def recall_causal_memory(rr, question: str) -> list[dict]:
    """Scan dummy AskLog via RelOp. QueRIE-style prior causal acts, not a chat ghost."""
    want = set(causal_candidates())
    words = {w for w in re.findall(r"[a-z0-9]+", (question or "").casefold()) if len(w) > 2}
    try:
        ran = rr.analytics.run_chain([{"op": "ask_log"}], plan_id="causal-recall")
    except Exception:
        return []
    cols = [str(c) for c in ran.get("columns") or []]
    idx = {c.casefold(): i for i, c in enumerate(cols)}
    hits: list[dict] = []
    for row in ran.get("rows") or []:
        composite = str(_row_get(row, idx, "composite") or "")
        pattern = str(_row_get(row, idx, "pattern") or "")
        if composite not in want and pattern not in {"causal_abduce", "causal_plan", "causal_explore"}:
            continue
        blob = f"{_row_get(row, idx, 'objective')} {_row_get(row, idx, 'question')}".casefold()
        overlap = len(words & set(re.findall(r"[a-z0-9]+", blob))) if words else 1
        if words and overlap < 1:
            continue
        raw_score = _row_get(row, idx, "score", None)
        try:
            score = float(raw_score) if raw_score is not None and raw_score != "" else 0.0
        except (TypeError, ValueError):
            score = 0.0
        hits.append(
            {
                "composite": composite,
                "pattern": pattern,
                "score": score,
                "overlap": overlap,
                "rowCount": _row_get(row, idx, "rowcount", 0),
            }
        )
    hits.sort(key=lambda h: (h["overlap"], h["score"]), reverse=True)
    return hits[:8]


def _goal_bonus(cid: str, goal: dict, amount: float) -> float:
    if (goal or {}).get("measure") and cid in {"causal_then_agg", "causal_then_intervene", "intervene_west_discount"}:
        return amount
    return 0.0


def abduce_causal(
    rr,
    question: str,
    *,
    column: str = "ProductName",
    n: int = 8,
    goal: dict | None = None,
    hinted: str | None = None,
) -> dict:
    """Enumerate legal causal RelOps, sandbox-score them, keep the winner. No SQL from the SLM."""
    from revolverelate.analytics.asklog import record_ask
    from revolverelate.analytics.primitives import get_composite

    query = (question or "").strip() or "sales fell because discounting"
    goal = goal if isinstance(goal, dict) else {}
    hinted = hinted or match_causal_composite(query)
    weights = _abduce_score_spec()
    prior = recall_causal_memory(rr, query)
    prior_ids = {str(p.get("composite") or "") for p in prior}
    ranked: list[dict] = []
    for cid in causal_candidates():
        try:
            spec = get_composite(cid)
            steps = bind_causal_steps(list(spec.get("steps") or []), query=query, column=column, n=n, goal=goal)
        except Exception:
            continue
        grammar = check_chain(steps)
        if not grammar.get("ok"):
            ranked.append(
                {
                    "composite": cid,
                    "score": 0.0,
                    "sandboxScore": 0.0,
                    "bonus": 0.0,
                    "status": "illegal",
                    "rowCount": 0,
                    "grammar": {k: grammar.get(k) for k in ("ok", "issues")},
                }
            )
            continue
        ran = rr.analytics.run_chain(steps, plan_id=f"causal-abduce-{cid}")
        sandbox = score_causal_rows(ran.get("columns"), ran.get("rows"), world_scale=weights["worldScale"])
        bonus = 0.0
        if cid == hinted:
            bonus += weights["hintBonus"]
        if cid in prior_ids:
            bonus += weights["memoryBonus"]
        bonus += _goal_bonus(cid, goal, weights["goalBonus"])
        total = sandbox + bonus
        try:
            record_ask(
                rr.sandbox,
                question=query,
                objective=query,
                ir=ran.get("ir"),
                status=ran.get("status") or "sandbox_ok",
                composite=cid,
                pattern="causal_abduce",
                score=total,
                row_count=int(ran.get("rowCount") or 0),
            )
        except Exception:
            pass
        ranked.append(
            {
                "composite": cid,
                "score": total,
                "sandboxScore": sandbox,
                "bonus": bonus,
                "status": ran.get("status"),
                "rowCount": ran.get("rowCount"),
                "sandboxOnly": bool(spec.get("sandboxOnly")),
                "grammar": {k: grammar.get(k) for k in ("ok", "issues")},
                "_ran": ran,
                "_steps": steps,
            }
        )
    ranked.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    top = ranked[0] if ranked else {}
    winner_ran = top.pop("_ran", {}) if top else {}
    winner_steps = top.pop("_steps", []) if top else []
    for row in ranked:
        row.pop("_ran", None)
        row.pop("_steps", None)
    winner = dict(top) if top else {}
    return {
        "kind": "causal_explore",
        "query": query,
        "goal": goal,
        "hinted": hinted,
        "composite": winner.get("composite"),
        "steps": winner_steps,
        "grammar": winner.get("grammar") or {"ok": False, "issues": ["no legal causal candidate"]},
        "candidates": ranked,
        "winner": winner,
        "memory": prior,
        "relop": {
            "status": winner_ran.get("status"),
            "sql": winner_ran.get("sql"),
            "params": winner_ran.get("params"),
            "columns": winner_ran.get("columns"),
            "rows": winner_ran.get("rows"),
            "rowCount": winner_ran.get("rowCount"),
            "id": winner_ran.get("id"),
            "target": "sandbox",
        },
        "sandboxOnly": False,
        "hint": "Goal-scored abduce over legal causal RelOps. Dummy picks the winner, then the same RelOp may replay live.",
    }
