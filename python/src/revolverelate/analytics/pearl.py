"""Pearl backdoor on fact RelOps. Dummy ticket, then live GLM and do() CASE.

Overlay is never promoted. CASE is a SELECT rewrite, not UPDATE.
Identification is only as good as the declared DAG in spec/causal-pearl.json.
"""

from __future__ import annotations

import json

from revolverelate.analytics.heuristic import (
    _binarize,
    _col_index,
    _series,
    bind_because,
    glm_odds_ratio,
    search_heuristic,
)
from revolverelate.catalog import spec_dir


def load_pearl_spec() -> dict:
    return json.loads((spec_dir() / "causal-pearl.json").read_text(encoding="utf-8"))


def identify(bind: dict | None = None) -> dict:
    spec = load_pearl_spec()
    bind = bind or {}
    treatment = str(bind.get("treatment") or spec.get("treatment") or "Discount")
    outcome = str(bind.get("outcome") or spec.get("outcome") or "Sales")
    z = [str(x) for x in spec.get("backdoor") or ["Category"]]
    edges = spec.get("dag", {}).get("edges") or []
    has_effect = any(
        str(e.get("from")).casefold() == treatment.casefold()
        and str(e.get("to")).casefold() == outcome.casefold()
        for e in edges
        if isinstance(e, dict)
    )
    return {
        "criterion": spec.get("criterion") or "backdoor",
        "identification": spec.get("identification") or "backdoor",
        "identifiable": bool(has_effect and z),
        "treatment": treatment,
        "outcome": outcome,
        "adjustment": z,
        "formula": spec.get("formula"),
        "assumptions": list(spec.get("assumptions") or []),
        "dag": spec.get("dag"),
    }


def backdoor_ate(columns, rows, treatment: str, outcome: str, adjustment: list[str]) -> dict:
    """Stratified ATE: Σ_z [E(Y|X=1,z) − E(Y|X=0,z)] P(Z=z). X is median-split treatment."""
    y = _series(columns, rows, outcome)
    x = _binarize(_series(columns, rows, treatment), side="high")
    z_name = adjustment[0] if adjustment else ""
    z = _series(columns, rows, z_name) if z_name and _col_index(columns, z_name) is not None else [""] * len(y)
    yf = []
    for v in y:
        try:
            yf.append(float(v))
        except (TypeError, ValueError):
            yf.append(None)
    strata: dict[str, list[tuple[int, float]]] = {}
    for xi, yi, zi in zip(x, yf, z):
        if yi is None:
            continue
        strata.setdefault(str(zi), []).append((xi, yi))
    parts = []
    n_used = 0
    ate = 0.0
    skipped = []
    for key, pairs in strata.items():
        ones = [yi for xi, yi in pairs if xi == 1]
        zeros = [yi for xi, yi in pairs if xi == 0]
        n_z = len(pairs)
        if not ones or not zeros:
            skipped.append(key)
            continue
        mu1 = sum(ones) / len(ones)
        mu0 = sum(zeros) / len(zeros)
        delta = mu1 - mu0
        parts.append({"z": key, "n": n_z, "mu1": mu1, "mu0": mu0, "ate": delta})
        ate += delta * n_z
        n_used += n_z
    if n_used:
        ate /= n_used
    d_bin = x
    y_bin = _binarize([v if v is not None else 0.0 for v in yf], side="high")
    or_ = glm_odds_ratio(y_bin, d_bin)
    return {
        "ate": ate if n_used else None,
        "n": len(yf),
        "nUsed": n_used,
        "strata": parts,
        "positivitySkipped": skipped,
        "glm": or_,
        "treatment": treatment,
        "outcome": outcome,
        "adjustment": adjustment,
    }


def _clip_world(ran: dict) -> dict:
    return {
        "status": ran.get("status"),
        "rowCount": ran.get("rowCount"),
        "columns": ran.get("columns"),
        "rows": ran.get("rows"),
        "sql": ran.get("sql"),
    }


def _promote_world(rr, plan_id: str, columns, bind: dict, ident: dict) -> dict:
    from revolverelate.errors import PromoteError

    try:
        promoted = rr.analytics.promote(plan_id)
        live = promoted.get("live") or {}
        cols = live.get("columns") or columns
        rows = live.get("rows") or []
        has_x = _col_index(cols, ident["treatment"]) is not None
        has_y = _col_index(cols, ident["outcome"]) is not None
        return {
            "ran": True,
            "rowCount": len(rows),
            "columns": cols,
            "rows": rows,
            "sql": live.get("sql"),
            "ate": backdoor_ate(cols, rows, ident["treatment"], ident["outcome"], ident["adjustment"])
            if rows and has_x and has_y
            else None,
            "glm": search_heuristic(rr.schema, cols, rows, bind)[:6] if rows and has_x and has_y else None,
        }
    except (PromoteError, Exception) as exc:
        return {"ran": False, "error": str(exc)[:240]}


def pearl(rr, question: str, *, live: bool = True, discourse: bool = False) -> dict:
    """Bind because-clause → backdoor identify → dummy GLM + CASE → live replay."""
    from revolverelate.analytics.asklog import record_ask

    spec = load_pearl_spec()
    bind = bind_because(rr.schema, question)
    ident = identify(bind)
    _ = discourse
    facts = rr.analytics.run_chain(composite=str((spec.get("facts") or {}).get("composite") or "pearl_backdoor_facts"))
    columns = facts.get("columns") or []
    rows = facts.get("rows") or []
    dummy_ate = backdoor_ate(columns, rows, ident["treatment"], ident["outcome"], ident["adjustment"])
    dummy_glm = search_heuristic(rr.schema, columns, rows, bind)
    world = rr.analytics.run_chain(composite=str((spec.get("do") or {}).get("composite") or "pearl_do_west"))
    try:
        record_ask(
            rr.sandbox,
            question=question,
            objective=question,
            ir=facts.get("ir"),
            status="sandbox_ok",
            composite="pearl_backdoor_facts",
            pattern="pearl_backdoor",
            score=float(dummy_ate.get("ate") or 0),
            row_count=int(facts.get("rowCount") or 0),
        )
    except Exception:
        pass
    live_facts: dict = {"ran": False}
    live_do: dict = {"ran": False}
    if live:
        if facts.get("status") == "sandbox_ok" and facts.get("id"):
            live_facts = _promote_world(rr, facts["id"], columns, bind, ident)
        if world.get("status") == "sandbox_ok" and world.get("id"):
            try:
                promoted = rr.analytics.promote(world["id"])
                live_do = {
                    "ran": True,
                    "rowCount": len((promoted.get("live") or {}).get("rows") or []),
                    "columns": (promoted.get("live") or {}).get("columns"),
                    "rows": (promoted.get("live") or {}).get("rows"),
                    "sql": (promoted.get("live") or {}).get("sql"),
                }
            except Exception as exc:
                live_do = {"ran": False, "error": str(exc)[:240]}
    return {
        "kind": "pearl",
        "query": question,
        "bind": bind,
        "identify": ident,
        "sandbox": {
            "facts": {"status": facts.get("status"), "rowCount": facts.get("rowCount"), "columns": columns},
            "ate": dummy_ate,
            "glm": dummy_glm[:6],
            "do": _clip_world(world),
        },
        "live": {"facts": live_facts, "do": live_do},
        "overlayPromoted": False,
        "sandboxOnly": False,
        "hint": "Backdoor ATE and GLM on facts; do() is CASE SELECT on dummy then live. Not UPDATE. Overlay stays sandbox-only.",
    }