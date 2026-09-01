"""Automine runner: RelOp reflect → catalog expand → rebuild → mine again."""

from __future__ import annotations

import json
import time
from pathlib import Path

from revolverelate.analytics.asklog import record_ask
from revolverelate.analytics.bind import bind_analytics_goal
from revolverelate.domain.gene import append_follow_on, list_symbols
from revolverelate.domain.kpi import bind_kpis, run_kpi
from revolverelate.domain.mine import catalog_targets, extract_targets, flatten_cells, load_automine_spec
from revolverelate.domain.etiology import collect_etiologies, etiology_candidates, merge_etiologies
from revolverelate.domain.reflect import goal_reached, next_family, next_pivot_column, splice_details, splice_question
from revolverelate.vector.overlay import OVERLAY


def _conn(rr):
    return getattr(rr.adapter, "_conn", None)


def _live_scan(rr, entity: str) -> dict:
    ran = rr.analytics.run_chain([{"op": "scan_entity", "entity": entity}], plan_id=f"automine-scan-{entity}")
    live = rr.replay_live(plan_id=ran.get("id")) if ran.get("status") == "sandbox_ok" else {"ran": False}
    return {
        "status": ran.get("status"),
        "id": ran.get("id"),
        "dummyRowCount": ran.get("rowCount"),
        "live": {
            "ran": bool(live.get("ran")),
            "rowCount": live.get("rowCount"),
            "columns": live.get("columns"),
            "rows": live.get("rows"),
        },
    }


def _reflect(rr, question: str, spec: dict) -> dict:
    bound = bind_analytics_goal(rr.schema, question)
    causal = rr.causal(question, live=True)
    kpi_id = str((spec.get("reflect") or {}).get("kpi") or "")
    kpi = None
    if kpi_id and any(k["id"] == kpi_id and k.get("available") for k in bind_kpis(rr.schema)):
        kpi = run_kpi(rr, kpi_id, live=True)
    scans = {}
    for entity in (spec.get("reflect") or {}).get("scanEntities") or []:
        if rr.schema.entity(entity) is None:
            continue
        scans[entity] = _live_scan(rr, entity)
    blob_parts = [
        question,
        flatten_cells((causal.get("relop") or {}).get("rows")),
        flatten_cells((causal.get("live") or {}).get("rows")),
        flatten_cells((kpi or {}).get("rows")),
        flatten_cells(((kpi or {}).get("live") or {}).get("rows")),
    ]
    for scan in scans.values():
        blob_parts.append(flatten_cells((scan.get("live") or {}).get("rows")))
    known = set()
    conn = _conn(rr)
    if conn is not None:
        known = list_symbols(conn)
    next_rows = extract_targets(" ".join(blob_parts), known=known, catalog=catalog_targets())
    try:
        record_ask(
            rr.sandbox,
            question=question,
            objective="automine-reflect",
            status="sandbox_ok",
            composite=str(causal.get("composite") or ""),
            pattern="automine",
            score=float((causal.get("live") or {}).get("rowCount") or 0),
            row_count=int((causal.get("live") or {}).get("rowCount") or 0),
        )
    except Exception:
        pass
    return {
        "bound": bound,
        "causal": {
            "composite": causal.get("composite"),
            "goal": causal.get("goal"),
            "dummyStatus": (causal.get("relop") or {}).get("status"),
            "dummyRowCount": (causal.get("relop") or {}).get("rowCount"),
            "live": {
                "ran": bool((causal.get("live") or {}).get("ran")),
                "rowCount": (causal.get("live") or {}).get("rowCount"),
                "columns": (causal.get("live") or {}).get("columns"),
                "rows": (causal.get("live") or {}).get("rows"),
            },
        },
        "kpi": {
            "id": kpi_id,
            "status": (kpi or {}).get("status"),
            "rows": (kpi or {}).get("rows"),
            "live": (kpi or {}).get("live"),
        }
        if kpi
        else None,
        "known": sorted(known),
        "next": [str(r.get("symbol") or "") for r in next_rows],
        "nextRecords": next_rows,
        "scans": {k: {"liveRowCount": (v.get("live") or {}).get("rowCount")} for k, v in scans.items()},
    }


def _expand(rr, records: list[dict], *, limit: int) -> list[str]:
    conn = _conn(rr)
    if conn is None or not records:
        return []
    added = append_follow_on(conn, records[: max(int(limit), 0)])
    try:
        record_ask(
            rr.sandbox,
            question=",".join(added),
            objective="automine-expand",
            status="sandbox_ok",
            pattern="automine",
            row_count=len(added),
        )
    except Exception:
        pass
    return added


def _save_state(workdir: Path, payload: dict) -> Path:
    dest = Path(workdir) / ".revolverelate" / "automine.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return dest


def run_automine(
    rr,
    question: str,
    *,
    passes: int | None = None,
    until_stable: bool | None = None,
    live: bool = True,
    report: bool = True,
) -> dict:
    """Mine → RelOp reflect → expand catalogued follow-ons → rebuild → mine again."""
    spec = load_automine_spec()
    stop = spec.get("stop") or {}
    hard = int(stop.get("hardMaxPasses") or 8)
    n = int(passes if passes is not None else stop.get("defaultPasses") or 3)
    if n <= 0:
        n = hard
    n = min(n, hard)
    stable_flag = stop.get("untilStable") if until_stable is None else until_stable
    per = int((spec.get("expand") or {}).get("maxNewPerPass") or 2)
    if not rr.cache.is_complete():
        rr.build()
    history: list[dict] = []
    mined: list[str] = []
    etiologies: list[dict] = []
    stable = False
    stop_reason = "maxPasses"
    ask = question
    last_ask = ""
    idle = 0
    for i in range(1, n + 1):
        reflect = _reflect(rr, ask, spec)
        added = _expand(rr, reflect.get("nextRecords") or [], limit=per) if live else []
        if added:
            rr.build(refresh=True)
            mined.extend(added)
        details = splice_details(reflect.get("causal"), proposed=reflect.get("next"), added=added)
        found = collect_etiologies(
            reflect.get("causal"),
            proposed=reflect.get("next"),
            added=added,
            pass_no=i,
        )
        etiologies = merge_etiologies(etiologies, found)
        pairs = int(details.get("livePairs") or 0)
        column = next_pivot_column(rr.schema, ask, live_pairs=pairs)
        family = next_family(i)
        nxt = splice_question(question, details, column=column, family=family)
        spliced = ask.casefold() != question.casefold()
        goal = goal_reached(spec, details=details, spliced=spliced, mined=mined, etiologies=etiologies)
        row = {
            "pass": i,
            "question": ask,
            "nextQuestion": nxt,
            "pivot": {"column": column, "family": family},
            "splice": details,
            "etiologies": found,
            "goal": goal,
            "bound": reflect.get("bound"),
            "causal": reflect.get("causal"),
            "kpi": reflect.get("kpi"),
            "known": reflect.get("known"),
            "proposed": reflect.get("next"),
            "added": added,
            "overlay": rr.overlay_stats() if rr.cache.is_complete() else {},
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        history.append(row)
        if goal.get("ok"):
            stable = True
            stop_reason = "goalReached"
            break
        if nxt.casefold() == last_ask.casefold() and not added:
            idle += 1
        else:
            idle = 0
        if stable_flag and not added and idle >= 1 and i > 1:
            stable = True
            stop_reason = "noNewTargets"
            break
        last_ask = ask
        ask = nxt
    state = {
        "kind": "automine",
        "question": question,
        "finalQuestion": ask,
        "passes": len(history),
        "stable": stable,
        "stop": stop_reason,
        "goal": (history[-1].get("goal") if history else {}),
        "identification": "none",
        "evidenceGrade": "heuristic",
        "conclusive": False,
        "etiologies": etiologies,
        "candidates": etiology_candidates(etiologies),
        "mined": mined,
        "overlayVirtual": OVERLAY,
        "businessEntities": [e.name for e in rr.schema.all_entities()],
        "history": history,
        "honesty": spec.get("honesty"),
    }
    if report:
        from revolverelate.domain.research import run_research

        state["report"] = run_research(state, workdir=rr.workdir)
    _save_state(rr.workdir, state)
    return state
