"""Composite chain grammar: phases, depth, legal forward motion."""

from __future__ import annotations

import json
from functools import lru_cache

from revolverelate.catalog import spec_dir
from revolverelate.errors import AskError


@lru_cache(maxsize=1)
def load_composite_rules() -> dict:
    return json.loads((spec_dir() / "analytics-composites.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _primitive_index() -> dict[str, dict]:
    tax = json.loads((spec_dir() / "analytics-primitives.json").read_text(encoding="utf-8"))
    return {p["id"]: p for p in tax["primitives"]}


def _phase_index() -> dict:
    rules = load_composite_rules()
    by_family: dict[str, dict] = {}
    late_ids: dict[str, int] = {}
    for phase in rules["phases"]:
        for fam in phase.get("families") or []:
            by_family[fam] = phase
        for pid in phase.get("lateIds") or []:
            late_ids[pid] = int(phase["rank"])
    return {
        "family": by_family,
        "late": late_ids,
        "early_project": 15,
        "resets": set(rules.get("resets") or []),
    }


def step_rank(pid: str) -> tuple[int, str, bool]:
    row = _primitive_index().get(pid)
    if not row:
        raise AskError(f"Unknown analytics primitive {pid!r}")
    family = row["family"]
    idx = _phase_index()
    reset = pid in idx["resets"]
    if pid in idx["late"]:
        return idx["late"][pid], family, reset
    if family == "project":
        return idx["early_project"], family, reset
    phase = idx["family"].get(family) or {"rank": 50}
    return int(phase["rank"]), family, reset


def check_chain(steps: list[dict]) -> dict:
    """Validate a primitive chain against spec/analytics-composites.json."""
    rules = load_composite_rules()
    depth = rules["depth"]
    issues: list[str] = []
    n = len(steps)
    if n < int(depth["min"]):
        issues.append(f"chain is empty (min {depth['min']})")
    if n > int(depth["hardMax"]):
        issues.append(f"chain depth {n} exceeds hardMax {depth['hardMax']}")
    last_rank = 0
    collapse_in_pass = False
    set_in_pass = False
    pass_no = 1
    ranks: list[int] = []
    for i, step in enumerate(steps):
        pid = str(step.get("op") or step.get("primitive") or step.get("id") or "")
        if not pid:
            issues.append(f"step {i} missing op")
            continue
        try:
            rank, family, reset = step_rank(pid)
        except AskError as exc:
            issues.append(str(exc))
            continue
        anytime = family in {"quality", "derive", "intent"}
        if reset and i > 0 and pid not in {"with_cte", "hypothesize"}:
            prev = str(steps[i - 1].get("op") or "")
            if prev != "with_cte":
                issues.append(f"step {i} {pid} resets without with_cte (start a second pass via with_cte)")
            else:
                pass_no += 1
                collapse_in_pass = False
                set_in_pass = False
                last_rank = 0
        elif pid in {"with_cte", "hypothesize"}:
            pass_no += 1
            collapse_in_pass = False
            set_in_pass = False
            last_rank = 0
            ranks.append(0)
            continue
        elif not anytime and rank < last_rank:
            if family == "restrict" and collapse_in_pass:
                rank = last_rank
            else:
                issues.append(f"step {i} {pid} goes backward ({rank} < {last_rank})")
        if family in {"aggregate", "stat", "hierarchy"}:
            if collapse_in_pass:
                issues.append(f"step {i} {pid} is a second collapse in the same pass — wrap with with_cte")
            collapse_in_pass = True
        if family == "set":
            if collapse_in_pass:
                issues.append(f"step {i} {pid} mixes set with collapse in one pass")
            set_in_pass = True
        if family == "aggregate" and set_in_pass:
            issues.append(f"step {i} {pid} mixes aggregate with set in one pass")
        last_rank = max(last_rank, rank)
        ranks.append(rank)
    return {
        "ok": not issues,
        "depth": n,
        "passes": pass_no,
        "typical": int(depth["typicalMin"]) <= n <= int(depth["typicalMax"]),
        "deep": n >= int(depth["deep"]),
        "hardMax": int(depth["hardMax"]),
        "ranks": ranks,
        "issues": issues,
        "rules": [r["id"] for r in rules.get("rules") or []],
    }


def assert_chain(steps: list[dict]) -> dict:
    report = check_chain(steps)
    if not report["ok"]:
        raise AskError("Illegal composite chain: " + "; ".join(report["issues"]))
    return report


def phase_families() -> list[str]:
    seen: list[str] = []
    for phase in load_composite_rules()["phases"]:
        for fam in phase.get("families") or []:
            if fam not in seen:
                seen.append(fam)
    return seen
