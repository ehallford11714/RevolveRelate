"""Automine runner: detect domain → recall → RelOp reflect → gate → evidence → remember → expand → rebuild → again.

Domain-neutral. Gene and finance ship as spec/domain-*.json; the runner asks the
domain for its catalog, KPI, scan entities, pivot columns, and evidence label.
Every pass carries a Kineteq-style gate verdict. A finished run is reused by key.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from revolverelate.analytics.asklog import record_ask
from revolverelate.analytics.bind import bind_analytics_goal
from revolverelate.domain.etiology import collect_etiologies, etiology_candidates, merge_etiologies
from revolverelate.domain.evidence_store import evidence_stats, recall_evidence, remember_evidence
from revolverelate.domain.kpi import bind_kpis, run_kpi
from revolverelate.domain.mine import extract_targets, flatten_cells, load_automine_spec
from revolverelate.domain.reflect import (
    gate_verdict,
    goal_reached,
    next_family,
    next_pivot_column,
    splice_details,
    splice_question,
)
from revolverelate.domain.registry import Domain, detect_domain, get_domain
from revolverelate.vector.embed import fingerprint
from revolverelate.vector.overlay import OVERLAY


def _conn(rr):
    return getattr(rr.adapter, "_conn", None)


def _state_path(workdir: str | Path) -> Path:
    return Path(workdir) / ".revolverelate" / "automine.json"


def reuse_key(domain_id: str, question: str) -> int:
    words = sorted({w for w in "".join(ch if ch.isalnum() else " " for ch in question.casefold()).split() if len(w) > 2})
    return fingerprint(f"{domain_id}|{' '.join(words)}")


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


def _reflect(rr, question: str, spec: dict, domain: Domain) -> dict:
    bound = bind_analytics_goal(rr.schema, question)
    error = None
    causal_n = int(domain.automine.get("causalN") or 8)
    try:
        causal = rr.causal(question, n=causal_n, live=True)
    except Exception as exc:  # the gate reports this as failed instead of aborting the loop
        causal, error = {}, str(exc)
    kpi_id = domain.kpi
    kpi = None
    if kpi_id and any(k["id"] == kpi_id and k.get("available") for k in bind_kpis(rr.schema)):
        try:
            kpi = run_kpi(rr, kpi_id, live=True)
        except Exception:
            kpi = None
    scans = {}
    for entity in domain.scan_entities:
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
    known = domain.known(_conn(rr))
    next_rows = extract_targets(" ".join(blob_parts), known=known, catalog=domain.catalog())
    try:
        record_ask(
            rr.sandbox,
            question=question,
            objective="automine-reflect",
            status="sandbox_ok" if not error else "failed",
            composite=str(causal.get("composite") or ""),
            pattern="automine",
            score=float((causal.get("live") or {}).get("rowCount") or 0),
            row_count=int((causal.get("live") or {}).get("rowCount") or 0),
        )
    except Exception:
        pass
    return {
        "bound": bound,
        "error": error,
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


def _expand(rr, domain: Domain, records: list[dict], *, limit: int) -> list[str]:
    conn = _conn(rr)
    if conn is None or not records:
        return []
    added = domain.append_follow_on(conn, records[: max(int(limit), 0)])
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
    dest = _state_path(workdir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return dest


def _load_reusable(workdir: Path, key: int, spec: dict) -> dict | None:
    rules = spec.get("reuse") if isinstance(spec.get("reuse"), dict) else {}
    if not rules.get("enabled", True):
        return None
    path = _state_path(workdir)
    if not path.exists():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if int(saved.get("reuseKey") or 0) != int(key) or saved.get("stop") != "goalReached":
        return None
    return saved


def resolve_domain(rr, *, domain: str | None = None) -> Domain:
    spec = load_automine_spec()
    found = detect_domain(rr.schema, prefer=domain)
    if found is None:
        fallback = str((spec.get("domains") or {}).get("fallback") or "gene")
        found = get_domain(fallback)
    return found


def run_automine(
    rr,
    question: str | None = None,
    *,
    passes: int | None = None,
    until_stable: bool | None = None,
    live: bool = True,
    report: bool = True,
    domain: str | None = None,
    rerun: bool = False,
) -> dict:
    """Detect domain → recall → reflect → gate → evidence → remember → expand → rebuild → again."""
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
    dom = resolve_domain(rr, domain=domain)
    question = (question or dom.default_question()).strip()
    key = reuse_key(dom.id, question)
    if not rerun:
        saved = _load_reusable(Path(rr.workdir), key, spec)
        if saved is not None:
            saved["reused"] = True
            return saved

    memory_cfg = spec.get("memory") if isinstance(spec.get("memory"), dict) else {}
    recall_n = int((spec.get("reflect") or {}).get("recallN") or 5)
    history: list[dict] = []
    mined: list[str] = []
    etiologies: list[dict] = []
    stable = False
    stop_reason = "maxPasses"
    ask = question
    last_ask = ""
    idle = 0
    verdicts: list[str] = []
    for i in range(1, n + 1):
        recall = recall_evidence(rr, ask, n=recall_n) if (spec.get("reflect") or {}).get("recall", True) else None
        reflect = _reflect(rr, ask, spec, dom)
        added = _expand(rr, dom, reflect.get("nextRecords") or [], limit=per) if live else []
        if added:
            rr.build(refresh=True)
            mined.extend(added)
            if memory_cfg.get("vector", True) and etiologies:
                # the rebuild recreates the dummy overlay; restore evidence memory from earlier passes
                remember_evidence(rr.sandbox, etiologies, domain=dom.id, question=question, pass_no=i)
        details = splice_details(reflect.get("causal"), proposed=reflect.get("next"), added=added, catalog=dom.catalog())
        found = collect_etiologies(
            reflect.get("causal"),
            proposed=reflect.get("next"),
            added=added,
            pass_no=i,
            catalog=dom.catalog(),
            kind=dom.evidence_kind,
            label=dom.evidence_label,
            symbol_entity=str(dom.automine.get("symbolEntity") or "Gene"),
            symbol_column=str(dom.automine.get("symbolColumn") or "Symbol"),
            driver_terms=dom.automine.get("driverTerms") if isinstance(dom.automine.get("driverTerms"), dict) else None,
        )
        slice_value = str(((reflect.get("bound") or {}).get("slice") or {}).get("value") or "").casefold()
        if slice_value:
            for row in found:
                row["inSlice"] = str(row.get("candidate") or "").casefold() == slice_value
            found.sort(key=lambda r: (not r.get("inSlice"), str(r.get("candidate") or "")))
        gate = gate_verdict(
            spec,
            details=details,
            etiologies=found,
            text_column=(reflect.get("bound") or {}).get("column"),
            error=reflect.get("error"),
        )
        for row in found:
            row["gate"] = gate["verdict"]
        verdicts.append(gate["verdict"])
        etiologies = merge_etiologies(etiologies, found)
        remembered = remember_evidence(rr.sandbox, found, domain=dom.id, question=ask, pass_no=i) if memory_cfg.get("vector", True) else 0
        pairs = int(details.get("livePairs") or 0)
        column = next_pivot_column(rr.schema, ask, live_pairs=pairs, columns=dom.pivot_columns)
        family = next_family(i, dom.families)
        nxt = splice_question(question, details, column=column, family=family)
        spliced = ask.casefold() != question.casefold()
        goal = goal_reached(spec, details=details, spliced=spliced, mined=mined, etiologies=etiologies)
        row = {
            "pass": i,
            "question": ask,
            "nextQuestion": nxt,
            "pivot": {"column": column, "family": family},
            "splice": details,
            "gate": gate,
            "recall": {"rowCount": (recall or {}).get("rowCount"), "rows": (recall or {}).get("rows")} if recall else None,
            "remembered": remembered,
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
    overall = "supported" if "supported" in verdicts else ("review_required" if "review_required" in verdicts else (verdicts[-1] if verdicts else "unknown"))
    state = {
        "kind": "automine",
        "domain": dom.id,
        "domainTitle": str(dom.spec.get("title") or dom.id),
        "evidenceKind": dom.evidence_kind,
        "evidenceLabel": dom.evidence_label,
        "candidateLabel": dom.candidate_label,
        "question": question,
        "finalQuestion": ask,
        "reuseKey": key,
        "reused": False,
        "passes": len(history),
        "stable": stable,
        "stop": stop_reason,
        "gate": {"overall": overall, "perPass": verdicts},
        "goal": (history[-1].get("goal") if history else {}),
        "identification": "none",
        "evidenceGrade": "heuristic",
        "conclusive": False,
        "etiologies": etiologies,
        "candidates": etiology_candidates(etiologies),
        "mined": mined,
        "memory": evidence_stats(rr.sandbox),
        "overlayVirtual": OVERLAY,
        "businessEntities": [e.name for e in rr.schema.all_entities()],
        "history": history,
        "honesty": " ".join(x for x in (spec.get("honesty"), dom.honesty) if x),
    }
    if report:
        from revolverelate.domain.research import run_research

        state["report"] = run_research(state, workdir=rr.workdir)
    _save_state(Path(rr.workdir), state)
    return state
