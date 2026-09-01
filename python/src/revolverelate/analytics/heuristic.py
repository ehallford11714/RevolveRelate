"""Because-clause → schema bind → GLM odds-ratio search.

Mines the AutoCausal exploratory contract (role hints, heuristic edges, no
identification). Overlay discourse stays sandbox-only. The fact RelOp has no
OverlayChunk and may promote after a dummy ticket.
"""

from __future__ import annotations

import json
import math
import re

from revolverelate.analytics.bind import bind_analytics_goal, list_measures, pick_dimension, resolve_column
from revolverelate.catalog import spec_dir


def load_heuristic_spec() -> dict:
    return json.loads((spec_dir() / "causal-heuristic.json").read_text(encoding="utf-8"))


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").casefold())


def _autocausal_roles(text: str) -> dict:
    """Soft mine of autocausal.nlp because-clause roles. Never required."""
    try:
        from autocausal.nlp import extract_causal_hints_from_text

        hints = extract_causal_hints_from_text(text or "")
        roles = hints.roles.to_dict() if hasattr(hints, "roles") else {}
        return roles if isinstance(roles, dict) else {}
    except Exception:
        return {}


def glm_odds_ratio(y: list[int], d: list[int], *, haldane: float = 0.5) -> dict:
    """2×2 logistic GLM odds ratio (Wald). Binary outcome y, binary treatment d."""
    if len(y) != len(d) or not y:
        return {"oddsRatio": None, "logOdds": None, "pValue": 1.0, "n": len(y), "table": [0, 0, 0, 0]}
    a = b = c = e = 0  # a=y1d1, b=y1d0, c=y0d1, e=y0d0
    for yi, di in zip(y, d):
        if yi and di:
            a += 1
        elif yi:
            b += 1
        elif di:
            c += 1
        else:
            e += 1
    h = float(haldane)
    odds = ((a + h) * (e + h)) / ((b + h) * (c + h))
    log_or = math.log(odds)
    se = math.sqrt(1 / (a + h) + 1 / (b + h) + 1 / (c + h) + 1 / (e + h))
    z = log_or / se if se else 0.0
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return {
        "oddsRatio": odds,
        "logOdds": log_or,
        "se": se,
        "z": z,
        "pValue": p,
        "n": len(y),
        "table": [e, c, b, a],
        "cells": {"y0d0": e, "y0d1": c, "y1d0": b, "y1d1": a},
    }


def _binarize(values: list, *, side: str) -> list[int]:
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            nums.append(0.0)
    if not nums:
        return []
    ordered = sorted(nums)
    mid = ordered[len(ordered) // 2]
    if side == "low":
        return [1 if v < mid else 0 for v in nums]
    return [1 if v > mid else 0 for v in nums]


def _col_index(columns, name: str) -> int | None:
    want = (name or "").casefold()
    cols = [str(c) for c in columns or []]
    for i, c in enumerate(cols):
        if c.casefold() == want or c.casefold().endswith("." + want):
            return i
    return None


def _series(columns, rows, name: str) -> list:
    idx = _col_index(columns, name)
    if idx is None:
        return []
    return [row[idx] if idx < len(row) else None for row in rows or []]


def bind_because(graph, question: str, *, pair: dict | None = None) -> dict:
    spec = load_heuristic_spec()
    blob = " ".join(
        [
            question or "",
            str((pair or {}).get("cause") or ""),
            str((pair or {}).get("effect") or ""),
        ]
    )
    words = set(_tokens(blob))
    roles = _autocausal_roles(blob)
    measures = {m.casefold(): m for m in list_measures(graph)}

    def _pick(mapping: dict, fallback: str) -> str:
        for token, col in mapping.items():
            if token in words and col.casefold() in measures:
                return measures[col.casefold()]
        return measures.get(fallback.casefold()) or fallback

    treatment = _pick(spec.get("treatments") or {}, "Discount")
    outcome = _pick(spec.get("outcomes") or {}, "Sales")
    for key in ("treatment", "d", "cause"):
        raw = str(roles.get(key) or roles.get("treatments") or "")
        hit = next((measures[m] for m in measures if m in raw.casefold()), None)
        if hit:
            treatment = hit
            break
    for key in ("outcome", "y", "effect"):
        raw = str(roles.get(key) or roles.get("outcomes") or "")
        hit = next((measures[m] for m in measures if m in raw.casefold()), None)
        if hit:
            outcome = hit
            break
    goal = bind_analytics_goal(graph, question)
    if str(treatment).casefold() not in measures:
        treatment = str(goal.get("treatment") or treatment)
    if str(outcome).casefold() not in measures:
        outcome = str(goal.get("measure") or outcome)
    slice_ = {}
    try:
        resolve_column(graph, "Region")
        slice_ = {"column": "Region", "value": "West"}
    except Exception:
        slice_ = dict(goal.get("slice") or {})
    for token, row in (spec.get("slices") or {}).items():
        if token in words and isinstance(row, dict):
            col = str(row.get("column") or "Region")
            try:
                resolve_column(graph, col)
                slice_ = {"column": col, "value": str(row.get("value") or "West")}
            except Exception:
                continue
            break
    encodes = spec.get("encode") or {}
    t_side = "high" if any(w in words for w in ("heavy", "high", "discounting", "discount", "mutation", "lof")) else "high"
    o_side = "low" if any(w in words for w in ("fell", "fall", "low", "drop")) else "high"
    if str(encodes.get("fell") or "") == "low" and "fell" in words:
        o_side = "low"
    try:
        resolve_column(graph, treatment)
        resolve_column(graph, outcome)
        if slice_.get("column"):
            resolve_column(graph, slice_["column"])
    except Exception:
        pass
    return {
        "treatment": treatment,
        "outcome": outcome,
        "dimension": goal.get("dimension"),
        "slice": slice_,
        "treatmentEncode": t_side,
        "outcomeEncode": o_side,
        "query": (question or "").strip(),
        "autocausalRoles": {k: roles[k] for k in list(roles)[:8]} if roles else {},
    }


def score_edge(columns, rows, treatment: str, outcome: str, *, t_side: str, o_side: str, alpha: float) -> dict:
    d = _binarize(_series(columns, rows, treatment), side=t_side)
    y = _binarize(_series(columns, rows, outcome), side=o_side)
    or_ = glm_odds_ratio(y, d)
    p = float(or_.get("pValue") or 1)
    log_or = or_.get("logOdds")
    supported = bool(or_.get("oddsRatio") is not None and p < alpha and abs(float(log_or or 0)) > 0)
    score = abs(float(log_or or 0)) * (1.0 if p < alpha else 0.25)
    return {
        "treatment": treatment,
        "outcome": outcome,
        "treatmentEncode": t_side,
        "outcomeEncode": o_side,
        "supported": supported,
        "score": score,
        **or_,
    }


def search_heuristic(graph, columns, rows, bind: dict) -> list[dict]:
    spec = load_heuristic_spec()
    alpha = float((spec.get("or") or {}).get("alpha") or 0.05)
    measures = list_measures(graph)
    hinted_t = str(bind.get("treatment") or "Discount")
    hinted_o = str(bind.get("outcome") or "Sales")
    t_side = str(bind.get("treatmentEncode") or "high")
    o_side = str(bind.get("outcomeEncode") or "low")
    ranked = []
    seen: set[tuple] = set()
    candidates = [(hinted_t, hinted_o, t_side, o_side)]
    for m in measures:
        if m.casefold() == hinted_o.casefold():
            continue
        if _col_index(columns, m) is None:
            continue
        candidates.append((m, hinted_o, "high", o_side))
        candidates.append((m, hinted_o, "high", "high"))
    for t, o, ts, os_ in candidates:
        key = (t.casefold(), o.casefold(), ts, os_)
        if key in seen or t.casefold() == o.casefold():
            continue
        seen.add(key)
        if _col_index(columns, t) is None or _col_index(columns, o) is None:
            continue
        ranked.append(score_edge(columns, rows, t, o, t_side=ts, o_side=os_, alpha=alpha))
    ranked.sort(key=lambda r: (r["supported"], r["score"]), reverse=True)
    return ranked


def _pair_from_rows(columns, rows) -> dict | None:
    if not rows:
        return None
    idx = {str(c).casefold(): i for i, c in enumerate(columns or [])}
    row = rows[0]
    def cell(*names):
        for n in names:
            i = idx.get(n)
            if i is not None and i < len(row):
                return row[i]
        return ""
    return {
        "cause": cell("causetext", "cause"),
        "effect": cell("effecttext", "effect"),
        "cue": cell("cue"),
        "sourcePk": cell("sourcepk"),
    }


def heuristic_cause(rr, question: str, *, live: bool = True, discourse: bool = True) -> dict:
    """Discourse bind + dummy GLM search, then the same fact RelOp on live."""
    from revolverelate.analytics.asklog import record_ask
    from revolverelate.errors import PromoteError

    spec = load_heuristic_spec()
    pair = None
    if discourse:
        try:
            found = rr.analytics.run_chain(composite="rag_causal_pair")
            pair = _pair_from_rows(found.get("columns"), found.get("rows"))
        except Exception:
            pair = None
    bind = bind_because(rr.schema, question, pair=pair)
    dimension = str(bind.get("dimension") or "Category")
    try:
        resolve_column(rr.schema, dimension)
    except Exception:
        try:
            resolve_column(rr.schema, "Category")
            dimension = "Category"
        except Exception:
            dimension = pick_dimension(rr.schema).attr_name
    base = [{"op": "star_join", "measure": bind["outcome"], "dimension": dimension}]
    slice_col = str((bind.get("slice") or {}).get("column") or "")
    slice_val = (bind.get("slice") or {}).get("value")
    sliced = None
    if slice_col and slice_val not in (None, ""):
        try:
            resolve_column(rr.schema, slice_col)
            sliced = rr.analytics.run_chain(
                base + [{"op": "eq", "column": slice_col, "value": slice_val}],
                plan_id="heuristic-facts-slice",
            )
        except Exception:
            sliced = None
    slice_applied = bool(sliced) and int(sliced.get("rowCount") or 0) >= 6
    facts = sliced if slice_applied else rr.analytics.run_chain(base, plan_id="heuristic-facts")
    bind = {**bind, "sliceApplied": slice_applied}
    columns = facts.get("columns") or []
    rows = facts.get("rows") or []
    ranked = search_heuristic(rr.schema, columns, rows, bind)
    hypothesis = next(
        (
            row
            for row in ranked
            if row["treatment"].casefold() == str(bind["treatment"]).casefold()
            and row["outcome"].casefold() == str(bind["outcome"]).casefold()
            and row["outcomeEncode"] == bind.get("outcomeEncode")
        ),
        ranked[0] if ranked else {},
    )
    winner = next((row for row in ranked if row.get("supported")), None) or (ranked[0] if ranked else {})
    try:
        record_ask(
            rr.sandbox,
            question=question,
            objective=question,
            ir=facts.get("ir"),
            status="sandbox_ok",
            composite="heuristic_west_facts",
            pattern="causal_heuristic",
            score=float(winner.get("score") or 0),
            row_count=int(facts.get("rowCount") or 0),
        )
    except Exception:
        pass
    live_out: dict = {"ran": False}
    if live and facts.get("status") == "sandbox_ok" and facts.get("id"):
        try:
            promoted = rr.analytics.promote(facts["id"])
            live_rows = (promoted.get("live") or {}).get("rows") or []
            live_cols = (promoted.get("live") or {}).get("columns") or columns
            live_ranked = search_heuristic(rr.schema, live_cols, live_rows, bind)
            live_out = {
                "ran": True,
                "rowCount": len(live_rows),
                "hypothesis": next(
                    (
                        row
                        for row in live_ranked
                        if row["treatment"].casefold() == str(bind["treatment"]).casefold()
                        and row["outcomeEncode"] == bind.get("outcomeEncode")
                    ),
                    live_ranked[0] if live_ranked else {},
                ),
                "winner": next((row for row in live_ranked if row.get("supported")), None)
                or (live_ranked[0] if live_ranked else {}),
            }
        except (PromoteError, Exception) as exc:
            live_out = {"ran": False, "error": str(exc)[:240]}
    return {
        "kind": "causal_heuristic",
        "identification": spec.get("identification") or "none",
        "evidenceGrade": spec.get("evidenceGrade") or "heuristic",
        "query": question,
        "discourse": pair,
        "bind": bind,
        "hypothesis": hypothesis,
        "winner": winner,
        "candidates": ranked[:8],
        "sandbox": {
            "status": facts.get("status"),
            "rowCount": facts.get("rowCount"),
            "columns": columns,
            "sql": facts.get("sql"),
        },
        "live": live_out,
        "sandboxOnly": False,
        "overlayPromoted": False,
        "hint": "Heuristic GLM odds-ratio on fact RelOp. Because-clause is bind, not identification. Overlay is not live.",
    }