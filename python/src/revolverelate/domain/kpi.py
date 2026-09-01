"""Bind named KPIs from spec/domain-*.json onto the live schema. RelOp recipes only."""

from __future__ import annotations

import json

from revolverelate.analytics.bind import resolve_column
from revolverelate.catalog import spec_dir
from revolverelate.schema.model import SchemaGraph


def load_domain_specs() -> list[dict]:
    rows = []
    root = spec_dir()
    for path in sorted(root.glob("domain-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _has_columns(graph: SchemaGraph, *names: str | None) -> bool:
    for name in names:
        if not name:
            continue
        try:
            resolve_column(graph, str(name))
        except Exception:
            return False
    return True


def bind_kpis(graph: SchemaGraph, *, domain: str | None = None) -> list[dict]:
    """Return KPIs whose measure/dimension columns exist on this catalogued DB."""
    out: list[dict] = []
    for spec in load_domain_specs():
        sid = str(spec.get("id") or "")
        if domain and sid.casefold() != domain.casefold():
            continue
        for kpi in spec.get("kpis") or []:
            if not isinstance(kpi, dict):
                continue
            args = dict(kpi.get("args") or {})
            available = _has_columns(graph, args.get("measure"), args.get("dimension"), args.get("dimension2"))
            out.append(
                {
                    "id": str(kpi.get("id") or ""),
                    "title": str(kpi.get("title") or kpi.get("id") or ""),
                    "recipe": str(kpi.get("recipe") or "sum_by_dimension"),
                    "args": args,
                    "available": available,
                    "domain": sid,
                }
            )
    return out


def run_kpi(rr, kpi_id: str, *, live: bool = True) -> dict:
    """Scaffold the KPI recipe on dummy, then replay live after a ticket."""
    hits = [k for k in bind_kpis(rr.schema) if k["id"] == kpi_id]
    if not hits:
        known = [k["id"] for k in bind_kpis(rr.schema)]
        raise KeyError(f"Unknown KPI {kpi_id!r}. Bound: {known}")
    kpi = hits[0]
    if not kpi.get("available"):
        raise KeyError(f"KPI {kpi_id!r} columns are not on this schema")
    plan = rr.analytics.run(str(kpi["recipe"]), **dict(kpi.get("args") or {}))
    live_out = rr.replay_live(plan_id=plan.get("id")) if live else {"ran": False}
    return {
        "kind": "kpi",
        "kpi": kpi,
        "status": plan.get("status"),
        "id": plan.get("id"),
        "columns": plan.get("columns"),
        "rows": plan.get("rows"),
        "rowCount": plan.get("rowCount"),
        "live": live_out,
    }
