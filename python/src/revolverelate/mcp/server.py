"""Dedicated MCP so any agent can drive RevolveRelate.

  python -m revolverelate.mcp
  revolverelate mcp

No extra dependency. Same Python as the host.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from revolverelate.__version__ import __version__
from revolverelate.buildcache import BuildCache
from revolverelate.catalog import list_engines
from revolverelate.compile.compiler import compile_ir
from revolverelate.mcp.install import host_configs
from revolverelate.mcp.protocol import run_mcp_loop
from revolverelate.revolverelate import RevolveRelate
from revolverelate.schema.model import SchemaGraph
from revolverelate.slm.jobs import schema_card, verify_relop
from revolverelate.slm.probe import slm_status

INSTRUCTIONS = """RevolveRelate MCP — any agent, any catalogued database.

A developer opens a repo, installs this package, and an agent can attach ANY catalogued DSN or corpus (warehouse, public DB, joined study table). build() infers atoms from that schema. Then the agent chains RelOp primitives and asks semantic or causal questions (why / what causes / nearest). Never invent SQL. Never invent Chroma filters. Never paste passwords.

Install once: pip install -e python   then rr_install (Cursor / VS Code / Claude / Windsurf JSON).

Mandatory loop:
1. rr_boot {dsn} — ANY catalogued DSN (sqlite path, postgresql://, mysql://, snowflake://, bigquery://, …). Parses schema, builds a dummy staging copy (no live PII), and automatically chunks non-PII text into OverlayChunk (Cue, Role, Text, SourcePk, …).
2. rr_question {question} — ANY English question. Auto-boots if dsn is present. Routes retrieve / why / what-if itself. Dummy RelOp first; the same RelOp then replays on live.
3. rr_rag {query, strategy=semantic|causal} — semantic or causal retrieve against the auto-chunked overlay, dummy then live.
4. rr_causal {question} — Causal RelOp (pair / attach / intervene / vs_world). Dummy then live.
5. rr_causal_explore {question} — try legal causal RelOps on dummy, keep the winner, replay live.
6. rr_causal_heuristic {question} — because-clause → GLM odds-ratio on facts, dummy then live.
7. rr_pearl {question} — backdoor identify + GLM + do() CASE, dummy then live. CASE is SELECT, not UPDATE.
8. rr_analytics_primitives / rr_analytics_chain — atoms and named composites (max depth 24). Bound KPIs from spec/domain-*.json appear on rr_boot when those columns exist (e.g. gene FASTA cases_by_gene).
9. rr_automine {question, passes} — mine → causal reflect → collect possible etiology evidence (heuristic, identification none) → splice cues/genes → pivot → re-cause → expand catalogued follow-ons. Then drafts a citation-grounded report (planner → researcher → reporter → validator). Evidence, not conclusive proof. Never SQL.
9b. rr_report — after automine, draft or reload the report. Citations are RelOp pairs, catalog NCBI/UniProt accessions, and bound KPI rows only. Local SLM or cloud API if present; otherwise a deterministic draft. Never invent papers.
9c. rr_kpi {kpi} — run a bound domain KPI (dummy then live). rr_gene — write the public FASTA pineoblastoma sample.
10. rr_promote only if you have a RelOp IR that already has a sandbox ticket (rr_question already replays live).

Gene / FASTA example: write a public NCBI protein sample (`python -m revolverelate gene`), rr_boot that sqlite, then rr_question "what causes pinealblastoma" (alias of pineoblastoma). RelOp binds Abstract/Cases/Symbol from the gene schema. Overlay chunks FASTA headers plus abstracts that contain because/therefore. This is bound retrieve + KPI, not a claim the engine discovered etiology.

If the user gives a DSN and a question in one turn, call rr_question with both.
"""

_DSN = {
    "type": "string",
    "description": "Database DSN (postgresql://, mysql://, sqlite path, warehouse URL). Default: REVOLVERELATE_DSN",
}
_WORKDIR = {
    "type": "string",
    "description": "Directory for .revolverelate/ cache (default: cwd or REVOLVERELATE_WORKDIR)",
}

_SESSIONS: dict[str, RevolveRelate] = {}


def _workdir(args: dict[str, Any]) -> Path:
    return Path(str(args.get("workdir") or os.environ.get("REVOLVERELATE_WORKDIR") or ".")).resolve()


def _dsn(args: dict[str, Any]) -> str:
    dsn = str(args.get("dsn") or os.environ.get("REVOLVERELATE_DSN") or "").strip()
    if dsn:
        return dsn
    sandbox = _workdir(args) / ".revolverelate" / "sandbox.sqlite"
    if sandbox.exists():
        return str(sandbox)
    raise ValueError("dsn is required (or set REVOLVERELATE_DSN) until a sandbox cache exists")


def _session(args: dict[str, Any], *, connect: bool = True) -> RevolveRelate:
    workdir = _workdir(args)
    key = str(workdir)
    inst = _SESSIONS.get(key)
    if inst is not None:
        return inst
    if not connect:
        raise ValueError("no session; call rr_connect, rr_build, or rr_boot")
    inst = RevolveRelate.connect(_dsn(args), workdir=workdir)
    _SESSIONS[key] = inst
    return inst


def _ensure_ready(args: dict[str, Any]) -> tuple[RevolveRelate | None, dict[str, Any] | None]:
    """Connect + build once if needed. Agents pass dsn + question in one call."""
    workdir = _workdir(args)
    cache = BuildCache(workdir)
    if cache.is_complete():
        try:
            return _session(args), None
        except Exception:
            pass
    try:
        dsn = _dsn(args)
    except ValueError as exc:
        return None, {
            "error": str(exc),
            "hint": "Call rr_boot or rr_question with dsn=postgresql://… | mysql://… | path/to.sqlite | snowflake://…",
            "engines": [e.id for e in list_engines()[:20]],
        }
    _SESSIONS.pop(str(workdir), None)
    rr = RevolveRelate.connect(dsn, workdir=workdir)
    _SESSIONS[str(workdir)] = rr
    if not rr.cache.is_complete():
        rr.build(
            refresh=bool(args.get("refresh")),
            rows_per_entity=int(args.get("rows") or 8),
            use_slm_policy=bool(args.get("use_slm_policy")),
        )
    return rr, None


def tool_health(args: dict[str, Any]) -> dict[str, Any]:
    cache = BuildCache(_workdir(args))
    slm = slm_status()
    return {
        "ok": True,
        "version": __version__,
        "workdir": str(_workdir(args)),
        "build": cache.load(),
        "complete": cache.is_complete(),
        "slm": slm,
        "hint": "If complete is false, call rr_boot with a DSN (any catalogued engine). Then rr_question.",
        "next": "rr_boot" if not cache.is_complete() else "rr_question",
    }


def tool_connect(args: dict[str, Any]) -> dict[str, Any]:
    rr = _session(args)
    return {
        "engine": rr.spec.engine.id,
        "family": rr.spec.engine.connection_family,
        "tier": rr.spec.engine.execute_tier,
        "dsn": rr.spec.redacted_dsn,
        "workdir": str(rr.workdir),
        "hint": "Call rr_build next. Do not query live yet.",
    }


def tool_build(args: dict[str, Any]) -> dict[str, Any]:
    workdir = _workdir(args)
    _SESSIONS.pop(str(workdir), None)
    rr = RevolveRelate.connect(_dsn(args), workdir=workdir)
    _SESSIONS[str(workdir)] = rr
    record = rr.build(
        refresh=bool(args.get("refresh")),
        rows_per_entity=int(args.get("rows") or 8),
        use_slm_policy=bool(args.get("use_slm_policy")),
    )
    return {
        **record,
        "graph": str(rr.cache.graph_path),
        "sandbox": str(rr.cache.sandbox_path),
        "hint": "Dummy duplicate is ready. Call rr_ask. Live promote is still blocked until a sandbox run is saved.",
    }


def tool_schema(args: dict[str, Any]) -> dict[str, Any]:
    rr = _session(args)
    if not rr.cache.is_complete() and rr._schema is None:
        return {"error": "insufficient: call rr_build first"}
    graph = rr.schema
    return {
        "engine": graph.engine,
        "entities": [e.to_dict() for e in graph.all_entities()],
        "relationships": [r.to_dict() for r in graph.relationships],
        "card": schema_card(graph, rr.policy if rr.cache.is_complete() or rr._policy else None),
    }


def tool_policy(args: dict[str, Any]) -> dict[str, Any]:
    rr = _session(args)
    if not rr.cache.is_complete() and rr._policy is None:
        return {"error": "insufficient: call rr_build first"}
    return rr.policy


def tool_install(_: dict[str, Any]) -> dict[str, Any]:
    return host_configs()


def tool_boot(args: dict[str, Any]) -> dict[str, Any]:
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    listed = rr.analytics.list() if rr.cache.is_complete() else {}
    return {
        "ok": True,
        "engine": rr.spec.engine.id,
        "family": rr.spec.engine.connection_family,
        "tier": rr.spec.engine.execute_tier,
        "dsn": rr.spec.redacted_dsn,
        "workdir": str(rr.workdir),
        "complete": rr.cache.is_complete(),
        "measures": listed.get("measures") or [],
        "dimensions": listed.get("dimensions") or [],
        "recipes": [r["id"] for r in (listed.get("recipes") or [])],
        "kpis": listed.get("kpis") or [],
        "composites": [c["id"] for c in (listed.get("composites") or [])],
        "families": [f["id"] for f in (listed.get("families") or [])],
        "overlay": rr.overlay_stats() if rr.cache.is_complete() else {},
        "hint": "Schema parsed and non-PII text chunked. Call rr_question with any business or causal question. Dummy stages; the same RelOp replays live.",
        "next": "rr_question",
    }


def route_question(question: str) -> str:
    """Pick ask / rag / causal / pearl from English. Agents can still call the dedicated tools."""
    q = (question or "").casefold()
    if any(tok in q for tok in ("pearl", "backdoor", "what if", "do(", "if we set", "if discount")):
        return "pearl"
    if any(tok in q for tok in ("because", "why ", "why did", "cause", "therefore", "intervene", "pineoblastoma", "pinealblastoma")):
        return "causal"
    if any(tok in q for tok in ("nearest", "similar", "retrieve", "looks like", "semantic", "chunk", "fasta")):
        return "rag"
    if "kpi" in q:
        return "kpi"
    return "ask"


def _clip_live(live: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(live or {})
    if row.get("rows") is not None:
        row["rows"] = (row.get("rows") or [])[:50]
    return row


def tool_question(args: dict[str, Any]) -> dict[str, Any]:
    """Any question: auto-boot, route retrieve/causal, dummy ticket, then live replay."""
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    question = str(args.get("question") or args.get("q") or "").strip()
    composite = args.get("composite")
    recipe = args.get("recipe")
    steps = args.get("steps")
    live = True if args.get("live") is None else bool(args.get("live"))
    if isinstance(steps, str):
        steps = json.loads(steps)
    if composite or steps:
        plan = rr.analytics.run_chain(steps, composite=composite)
        live_out = rr.replay_live(plan_id=plan.get("id")) if live else {"ran": False}
        return {
            "mode": "chain",
            "target": "sandbox",
            "id": plan["id"],
            "status": plan["status"],
            "question": question or composite,
            "steps": plan.get("steps"),
            "chainCheck": plan.get("chainCheck"),
            "sql": plan.get("sql"),
            "columns": plan.get("columns"),
            "rows": (plan.get("rows") or [])[:50],
            "rowCount": plan.get("rowCount"),
            "live": _clip_live(live_out),
            "promoted": bool(live_out.get("ran")),
            "hint": "Dummy staged the RelOp. live is the same plan on the real database.",
        }
    if recipe:
        binds = {
            k: args[k]
            for k in ("measure", "dimension", "dimension2", "value", "year", "n", "threshold", "min", "left", "right")
            if args.get(k) is not None
        }
        plan = rr.analytics.run(str(recipe), **binds)
        live_out = rr.replay_live(plan_id=plan.get("id")) if live else {"ran": False}
        return {
            "mode": "recipe",
            "target": "sandbox",
            "id": plan["id"],
            "status": plan["status"],
            "question": question or recipe,
            "sql": plan.get("sql"),
            "columns": plan.get("columns"),
            "rows": (plan.get("rows") or [])[:50],
            "rowCount": plan.get("rowCount"),
            "live": _clip_live(live_out),
            "promoted": bool(live_out.get("ran")),
            "hint": "Dummy staged the RelOp. live is the same plan on the real database.",
        }
    if not question:
        return {
            "error": "question, composite, recipe, or steps is required",
            "composites": [c["id"] for c in rr.analytics.list().get("composites") or []],
            "recipes": [r["id"] for r in rr.analytics.list().get("recipes") or []],
        }
    intent = str(args.get("intent") or route_question(question))
    if intent == "pearl":
        return {**tool_pearl({**args, "question": question}), "routed": "pearl"}
    if intent == "causal":
        return {**tool_causal({**args, "question": question}), "routed": "causal"}
    if intent == "rag":
        return {**tool_rag({**args, "query": question}), "routed": "rag"}
    if intent == "kpi":
        from revolverelate.domain.kpi import bind_kpis, run_kpi

        q = question.casefold()
        hits = [k for k in bind_kpis(rr.schema) if k.get("available")]
        kpi = next((k for k in hits if k["id"] in q or k["id"].replace("_", " ") in q), None)
        if kpi is None and hits:
            kpi = hits[0]
        if kpi is None:
            return {"error": "no KPI columns on this schema", "kpis": bind_kpis(rr.schema), "routed": "kpi"}
        out = run_kpi(rr, kpi["id"], live=live)
        return {
            "mode": "kpi",
            "routed": "kpi",
            "target": "sandbox",
            "question": question,
            "kpi": out.get("kpi"),
            "id": out.get("id"),
            "status": out.get("status"),
            "columns": out.get("columns"),
            "rows": (out.get("rows") or [])[:50],
            "rowCount": out.get("rowCount"),
            "live": _clip_live(out.get("live") if isinstance(out.get("live"), dict) else {}),
            "promoted": bool((out.get("live") or {}).get("ran")),
            "hint": "Bound domain KPI recipe on dummy, then the same RelOp on live.",
        }
    result = rr.ask(question)
    live_out = rr.replay_live(ir=result.get("ir")) if live else {"ran": False}
    return {
        "mode": "ask",
        "target": "sandbox",
        "routed": "ask",
        "question": question,
        "ir": result["ir"],
        "sql": result["sql"],
        "params": result["params"],
        "columns": result["columns"],
        "rows": result["rows"][:50],
        "validated": True,
        "live": _clip_live(live_out),
        "promoted": bool(live_out.get("ran")),
        "hint": "Dummy staged the RelOp. live is the same plan on the real database.",
    }


def tool_ask(args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or "").strip()
    if not question:
        return {"error": "question is required"}
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    result = rr.ask(question)
    return {
        "target": "sandbox",
        "ir": result["ir"],
        "sql": result["sql"],
        "params": result["params"],
        "columns": result["columns"],
        "rows": result["rows"][:50],
        "validated": True,
        "hint": "RelOp ran on dummy data. Call rr_promote only if you intend a live replay.",
    }


def tool_compile(args: dict[str, Any]) -> dict[str, Any]:
    ir = args.get("ir")
    if isinstance(ir, str):
        ir = json.loads(ir)
    if not isinstance(ir, dict):
        return {"error": "ir object required"}
    engine = str(args.get("engine") or "sqlite")
    workdir = _workdir(args)
    cache = BuildCache(workdir)
    if cache.is_complete():
        graph = SchemaGraph.from_dict(json.loads(cache.graph_path.read_text(encoding="utf-8")))
        from revolverelate.ir.validate import validate_ir

        validate_ir(ir, graph)
    sql, params = compile_ir(ir, engine)
    return {"sql": sql, "params": params, "engine": engine}


def tool_validate(args: dict[str, Any]) -> dict[str, Any]:
    rr = _session(args)
    ir = args.get("ir")
    if isinstance(ir, str):
        ir = json.loads(ir)
    if not isinstance(ir, dict):
        return {"error": "ir object required"}
    issues = verify_relop(ir, rr.schema)
    return {"ok": not issues, "issues": issues}


def tool_sandbox(args: dict[str, Any]) -> dict[str, Any]:
    rr = _session(args)
    if not rr.cache.is_complete():
        return {"error": "insufficient: call rr_build first"}
    ir = args.get("ir")
    if isinstance(ir, str):
        ir = json.loads(ir)
    if not ir and args.get("question"):
        return tool_ask(args)
    if not isinstance(ir, dict):
        return {"error": "ir or question required"}
    sql, params, columns, rows = rr.sandbox.run_ir(ir)
    return {"sql": sql, "params": params, "columns": columns, "rows": rows[:50], "target": "sandbox"}


def tool_promote(args: dict[str, Any]) -> dict[str, Any]:
    rr = _session(args)
    ir = args.get("ir")
    if isinstance(ir, str):
        ir = json.loads(ir)
    if not isinstance(ir, dict):
        return {"error": "ir object required"}
    try:
        return rr.promote(ir, allow_live=bool(args.get("allow_live")))
    except Exception as exc:
        return {"error": str(exc), "isError": True}


def tool_engines(_: dict[str, Any]) -> dict[str, Any]:
    engines = list_engines()
    return {
        "count": len(engines),
        "engines": [
            {
                "id": e.id,
                "family": e.family,
                "emitFamily": e.emit_family,
                "tier": e.execute_tier,
                "description": e.description,
            }
            for e in engines
        ],
    }


def tool_slm(_: dict[str, Any]) -> dict[str, Any]:
    return slm_status()


def tool_analytics_list(args: dict[str, Any]) -> dict[str, Any]:
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    return rr.analytics.list()


def tool_analytics_scaffold(args: dict[str, Any]) -> dict[str, Any]:
    recipe = str(args.get("recipe") or "").strip()
    if not recipe:
        return {"error": "recipe is required"}
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    if not rr.cache.is_complete():
        return {"error": "build cache is not complete", "hint": "Call rr_boot once"}
    binds = {k: args[k] for k in ("measure", "dimension", "dimension2", "value", "year", "n", "threshold", "min", "left", "right") if args.get(k) is not None}
    plan = rr.analytics.scaffold(recipe, **binds)
    return {
        "id": plan["id"],
        "recipe": plan["recipe"],
        "status": plan["status"],
        "ir": plan["ir"],
        "hint": "RelOp is scaffolded only. Call rr_analytics_rollout next — do not promote yet.",
    }


def tool_analytics_rollout(args: dict[str, Any]) -> dict[str, Any]:
    plan_id = str(args.get("plan") or args.get("id") or "").strip()
    if not plan_id:
        return {"error": "plan id is required"}
    rr = _session(args)
    if not rr.cache.is_complete():
        return {"error": "build cache is not complete"}
    plan = rr.analytics.rollout(plan_id)
    return {
        "id": plan["id"],
        "status": plan["status"],
        "target": "sandbox",
        "sql": plan.get("sql"),
        "rowCount": plan.get("rowCount"),
        "columns": plan.get("columns"),
        "rows": (plan.get("rows") or [])[:50],
        "hint": "Validated on the dummy duplicate. Call rr_analytics_promote only if you intend a live replay.",
    }


def tool_analytics_promote(args: dict[str, Any]) -> dict[str, Any]:
    plan_id = str(args.get("plan") or args.get("id") or "").strip()
    if not plan_id:
        return {"error": "plan id is required"}
    rr = _session(args)
    try:
        return rr.analytics.promote(plan_id, allow_live=bool(args.get("allow_live")))
    except Exception as exc:
        return {"error": str(exc), "isError": True}


def tool_analytics_primitives(args: dict[str, Any]) -> dict[str, Any]:
    rr = _session(args)
    if not rr.cache.is_complete():
        from revolverelate.analytics.primitives import list_composites, list_families, list_primitives

        return {
            "families": list_families(),
            "primitives": list_primitives(),
            "composites": list_composites(),
            "hint": "Schema binds appear after rr_build. Taxonomy is spec-first and does not need a live database.",
        }
    listed = rr.analytics.list()
    return {k: listed[k] for k in ("families", "primitives", "composites", "measures", "dimensions") if k in listed}


def tool_analytics_chain(args: dict[str, Any]) -> dict[str, Any]:
    steps = args.get("steps")
    composite = args.get("composite")
    if isinstance(steps, str):
        steps = json.loads(steps)
    if not steps and not composite:
        return {"error": "steps or composite is required"}
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    if not rr.cache.is_complete():
        return {"error": "build cache is not complete", "hint": "Call rr_boot once"}
    if args.get("rollout"):
        plan = rr.analytics.run_chain(steps, composite=composite)
    else:
        plan = rr.analytics.scaffold_chain(steps, composite=composite)
    return {
        "id": plan["id"],
        "recipe": plan.get("recipe"),
        "status": plan["status"],
        "steps": plan.get("steps"),
        "ir": plan.get("ir"),
        "sql": plan.get("sql"),
        "rowCount": plan.get("rowCount"),
        "columns": plan.get("columns"),
        "rows": (plan.get("rows") or [])[:50],
        "hint": "RelOp only. Roll out on the dummy sandbox before promote.",
    }


def tool_rag(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or args.get("question") or "").strip()
    if not query:
        return {"error": "query is required", "hint": "rr_rag query='bookcase binders' strategy=semantic"}
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    result = rr.rag(
        query,
        strategy=str(args.get("strategy") or "semantic"),
        column=str(args.get("column") or "ProductName"),
        n=int(args.get("n") or 5),
        live=True if args.get("live") is None else bool(args.get("live")),
    )
    relop = result.get("relop") or {}
    return {
        "mode": "rag",
        "sandboxOnly": False,
        "query": result.get("query"),
        "strategy": result.get("strategy"),
        "backend": result.get("backend"),
        "relop": {k: relop.get(k) for k in ("status", "sql", "columns", "rowCount") if k in relop},
        "rows": (relop.get("rows") or [])[:20],
        "live": _clip_live(result.get("live")),
        "overlayPromoted": bool((result.get("live") or {}).get("ran")),
        "chroma": (result.get("chroma") or [])[:20],
        "hint": result.get("hint"),
    }


def tool_causal(args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or args.get("query") or "").strip()
    if not question:
        return {"error": "question is required", "hint": "rr_causal question='sales fell because discounting — what if West discount were zero'"}
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    result = rr.causal(
        question,
        column=str(args.get("column") or "ProductName"),
        n=int(args.get("n") or 8),
        explore=bool(args.get("explore")),
        live=True if args.get("live") is None else bool(args.get("live")),
    )
    if result.get("kind") == "causal_explore":
        return _causal_explore_payload(result)
    relop = result.get("relop") or {}
    return {
        "mode": "causal",
        "sandboxOnly": False,
        "query": result.get("query"),
        "goal": result.get("goal"),
        "composite": result.get("composite"),
        "steps": result.get("steps"),
        "grammar": result.get("grammar"),
        "backend": result.get("backend"),
        "relop": {k: relop.get(k) for k in ("status", "sql", "columns", "rowCount") if k in relop},
        "rows": (relop.get("rows") or [])[:20],
        "live": _clip_live(result.get("live")),
        "overlayPromoted": bool((result.get("live") or {}).get("ran")),
        "chroma": (result.get("chroma") or [])[:20],
        "hint": result.get("hint"),
    }


def _causal_explore_payload(result: dict[str, Any]) -> dict[str, Any]:
    relop = result.get("relop") or {}
    return {
        "mode": "causal_explore",
        "sandboxOnly": False,
        "live": _clip_live(result.get("live")),
        "overlayPromoted": bool((result.get("live") or {}).get("ran")),
        "query": result.get("query"),
        "goal": result.get("goal"),
        "composite": result.get("composite"),
        "hinted": result.get("hinted"),
        "steps": result.get("steps"),
        "grammar": result.get("grammar"),
        "winner": result.get("winner"),
        "candidates": result.get("candidates") or [],
        "memory": result.get("memory") or [],
        "backend": result.get("backend"),
        "relop": {k: relop.get(k) for k in ("status", "sql", "columns", "rowCount") if k in relop},
        "rows": (relop.get("rows") or [])[:20],
        "chroma": (result.get("chroma") or [])[:20],
        "hint": result.get("hint"),
    }


def tool_causal_explore(args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or args.get("query") or "").strip()
    if not question:
        return {"error": "question is required", "hint": "rr_causal_explore question='sales fell because discounting'"}
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    result = rr.causal_explore(question, column=str(args.get("column") or "ProductName"), n=int(args.get("n") or 8))
    return _causal_explore_payload(result)


def tool_causal_heuristic(args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or args.get("query") or "").strip()
    if not question:
        return {"error": "question is required", "hint": "rr_causal_heuristic question='why did West sales fall because discounting'"}
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    live = args.get("live")
    result = rr.heuristic_cause(
        question,
        live=True if live is None else bool(live),
        discourse=bool(args.get("discourse", True)),
    )
    return {
        "mode": "causal_heuristic",
        "identification": result.get("identification"),
        "evidenceGrade": result.get("evidenceGrade"),
        "query": result.get("query"),
        "bind": result.get("bind"),
        "discourse": result.get("discourse"),
        "hypothesis": result.get("hypothesis"),
        "winner": result.get("winner"),
        "candidates": result.get("candidates") or [],
        "sandbox": result.get("sandbox"),
        "live": result.get("live"),
        "overlayPromoted": False,
        "hint": result.get("hint"),
    }


def tool_pearl(args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or args.get("query") or "").strip()
    if not question:
        return {"error": "question is required", "hint": "rr_pearl question='what if West discount were zero'"}
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    live = args.get("live")
    result = rr.pearl(
        question,
        live=True if live is None else bool(live),
        discourse=bool(args.get("discourse", False)),
    )
    return {
        "mode": "pearl",
        "kind": result.get("kind"),
        "query": result.get("query"),
        "bind": result.get("bind"),
        "identify": result.get("identify"),
        "sandbox": result.get("sandbox"),
        "live": result.get("live"),
        "overlayPromoted": False,
        "sandboxOnly": False,
        "hint": result.get("hint"),
    }


def tool_chroma(args: dict[str, Any]) -> dict[str, Any]:
    from revolverelate.vector.chroma_store import chroma_available, chroma_status, sync_chroma

    action = str(args.get("action") or "status").casefold()
    if action == "sync":
        rr, err = _ensure_ready(args)
        if err:
            return err
        assert rr is not None
        if not chroma_available():
            return {"ok": False, "hint": "pip install -e python[chroma]"}
        synced = sync_chroma(rr.sandbox, rr.workdir)
        return {**synced, "hint": "Dummy OverlayChunk → local Chroma. RelOp unchanged."}
    try:
        rr = _session(args)
        return chroma_status(rr.workdir)
    except Exception:
        return {"available": chroma_available(), "hint": "Call rr_boot first, then rr_chroma action=sync"}


def tool_kpi(args: dict[str, Any]) -> dict[str, Any]:
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    from revolverelate.domain.kpi import bind_kpis, run_kpi

    kpi_id = str(args.get("kpi") or args.get("id") or "").strip()
    if not kpi_id:
        return {"error": "kpi is required", "kpis": bind_kpis(rr.schema)}
    live = True if args.get("live") is None else bool(args.get("live"))
    out = run_kpi(rr, kpi_id, live=live)
    return {
        "mode": "kpi",
        "kind": "kpi",
        "kpi": out.get("kpi"),
        "id": out.get("id"),
        "status": out.get("status"),
        "columns": out.get("columns"),
        "rows": (out.get("rows") or [])[:50],
        "rowCount": out.get("rowCount"),
        "live": _clip_live(out.get("live") if isinstance(out.get("live"), dict) else {}),
        "hint": "Bound domain KPI recipe on dummy, then the same RelOp on live.",
    }


def tool_gene(args: dict[str, Any]) -> dict[str, Any]:
    from revolverelate.domain.gene import write_gene_pineal

    dest = Path(str(args.get("dest") or _workdir(args) / "gene.sqlite"))
    path = write_gene_pineal(dest)
    return {
        "mode": "gene",
        "path": str(path),
        "hint": "Public NCBI FASTA pineoblastoma sample. rr_boot this path, then rr_question / rr_automine.",
        "next": "rr_boot",
    }


def tool_automine(args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or args.get("query") or "what causes pinealblastoma").strip()
    rr, err = _ensure_ready(args)
    if err:
        return err
    assert rr is not None
    passes = args.get("passes")
    out = rr.automine(
        question,
        passes=int(passes) if passes is not None else None,
        until_stable=None if args.get("untilStable") is None else bool(args.get("untilStable")),
    )
    history = []
    for row in out.get("history") or []:
        history.append(
            {
                "pass": row.get("pass"),
                "question": row.get("question"),
                "nextQuestion": row.get("nextQuestion"),
                "pivot": row.get("pivot"),
                "splice": row.get("splice"),
                "etiologies": row.get("etiologies"),
                "goal": row.get("goal"),
                "proposed": row.get("proposed"),
                "added": row.get("added"),
                "known": row.get("known"),
                "causalLive": (row.get("causal") or {}).get("live"),
                "kpiLive": ((row.get("kpi") or {}).get("live") or {}),
            }
        )
    return {
        "mode": "automine",
        "kind": "automine",
        "question": out.get("question"),
        "finalQuestion": out.get("finalQuestion"),
        "passes": out.get("passes"),
        "stable": out.get("stable"),
        "stop": out.get("stop"),
        "goal": out.get("goal"),
        "identification": "none",
        "evidenceGrade": "heuristic",
        "conclusive": False,
        "etiologies": out.get("etiologies") or [],
        "candidates": out.get("candidates") or [],
        "mined": out.get("mined"),
        "businessEntities": out.get("businessEntities"),
        "history": history,
        "honesty": out.get("honesty"),
        "report": _report_view(out.get("report")),
        "hint": "Possible etiology evidence from live RelOp pairs. Identification is none — not conclusive proof. Call rr_report to re-draft from the saved handoff.",
    }


def _report_view(report: dict | None) -> dict | None:
    if not isinstance(report, dict):
        return None
    return {
        "kind": "research_report",
        "title": report.get("title"),
        "markdown": report.get("markdown"),
        "citations": report.get("citations") or [],
        "agents": report.get("agents") or [],
        "validation": report.get("validation") or {},
        "paths": report.get("paths") or {},
        "identification": "none",
        "evidenceGrade": "heuristic",
        "conclusive": False,
        "honesty": report.get("honesty"),
        "llm": report.get("llm"),
    }


def tool_report(args: dict[str, Any]) -> dict[str, Any]:
    from revolverelate.domain.research import run_research

    workdir = _workdir(args)
    saved = workdir / ".revolverelate" / "automine.json"
    state = args.get("state") if isinstance(args.get("state"), dict) else None
    if state is None and saved.exists() and not args.get("rerun"):
        state = json.loads(saved.read_text(encoding="utf-8"))
    if state is None:
        mined = tool_automine(args)
        if mined.get("error"):
            return mined
        view = mined.get("report")
        if view:
            return {"mode": "report", **view, "question": mined.get("question")}
        return {"error": "automine did not produce a report", "hint": "Call rr_automine first."}
    report = run_research(state, workdir=workdir)
    return {"mode": "report", **(_report_view(report) or {}), "question": state.get("question")}


MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "rr_install",
        "description": "Return MCP install JSON for Cursor, VS Code, Claude Desktop, Claude Code, Windsurf, and generic hosts. No database required.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rr_boot",
        "description": "Link ANY catalogued database: connect DSN, build dummy sandbox once, return measures/dimensions/recipes/composites. sqlite path, postgresql://, mysql://, snowflake://, bigquery://, … Does not copy live PII.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dsn": _DSN,
                "workdir": _WORKDIR,
                "refresh": {"type": "boolean"},
                "rows": {"type": "integer"},
            },
            "required": ["dsn"],
        },
    },
    {
        "name": "rr_question",
        "description": "Ask ANY business question. Auto-boots if dsn is given and the cache is empty. Pass question (English), or recipe, or composite/steps. RelOp only — never SQL. Runs on the dummy sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Natural-language business question"},
                "composite": {"type": "string", "description": "Named composite from spec/analytics-primitives.json"},
                "recipe": {"type": "string", "description": "Named recipe such as sum_by_dimension"},
                "steps": {"type": "array", "items": {"type": "object"}},
                "measure": {"type": "string"},
                "dimension": {"type": "string"},
                "dimension2": {"type": "string"},
                "value": {"type": "string"},
                "year": {"type": "string"},
                "n": {"type": "integer"},
                "threshold": {"type": "number"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_health",
        "description": "Build-cache status, SLM probe, and whether live promote is allowed yet.",
        "inputSchema": {"type": "object", "properties": {"workdir": _WORKDIR}},
    },
    {
        "name": "rr_connect",
        "description": "Attach a database DSN. Does not copy live data.",
        "inputSchema": {
            "type": "object",
            "properties": {"dsn": _DSN, "workdir": _WORKDIR},
            "required": ["dsn"],
        },
    },
    {
        "name": "rr_build",
        "description": "Read live schema, impute primitives, write .rrgraph.json, create local dummy-data duplicate. Run once; later calls reuse the cache unless refresh=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dsn": _DSN,
                "workdir": _WORKDIR,
                "refresh": {"type": "boolean"},
                "rows": {"type": "integer", "description": "Dummy rows per table (default 8)"},
                "use_slm_policy": {"type": "boolean"},
            },
        },
    },
    {
        "name": "rr_schema",
        "description": "Return the cached schema graph and a schema card (critical columns omitted).",
        "inputSchema": {"type": "object", "properties": {"workdir": _WORKDIR, "dsn": _DSN}},
    },
    {
        "name": "rr_policy",
        "description": "Return the accepted query policy (capabilities and sensitivity tags).",
        "inputSchema": {"type": "object", "properties": {"workdir": _WORKDIR, "dsn": _DSN}},
    },
    {
        "name": "rr_ask",
        "description": "Natural language → relational algebra (local SLM if available) → dialect SQL → execute on the dummy sandbox. Never writes SQL from the model.",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string"}, "dsn": _DSN, "workdir": _WORKDIR},
            "required": ["question"],
        },
    },
    {
        "name": "rr_compile",
        "description": "Deterministic RelOp → dialect SQL. Model-agnostic parser. No execute.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ir": {"type": "object"},
                "engine": {"type": "string", "description": "postgres, mysql, sqlite, snowflake, bigquery, tds, ..."},
                "workdir": _WORKDIR,
            },
            "required": ["ir"],
        },
    },
    {
        "name": "rr_validate",
        "description": "Validate RelOp against schema primitives (and optionally the SLM verifier).",
        "inputSchema": {
            "type": "object",
            "properties": {"ir": {"type": "object"}, "dsn": _DSN, "workdir": _WORKDIR},
            "required": ["ir"],
        },
    },
    {
        "name": "rr_sandbox",
        "description": "Run RelOp or a question against the local dummy duplicate only.",
        "inputSchema": {
            "type": "object",
            "properties": {"ir": {"type": "object"}, "question": {"type": "string"}, "dsn": _DSN, "workdir": _WORKDIR},
        },
    },
    {
        "name": "rr_promote",
        "description": "Replay a sandbox-validated RelOp on live. Blocked until build cache is complete and the dummy run was saved. Mutations need allow_live=true and mutate_live.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ir": {"type": "object"},
                "allow_live": {"type": "boolean"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
            "required": ["ir"],
        },
    },
    {
        "name": "rr_engines",
        "description": "List catalogued engines (compile-all, execute by tier).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rr_slm",
        "description": "Probe the best local model (Ollama / LM Studio) or cloud OpenAI-compatible API.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "rr_analytics_list",
        "description": "List analytics recipes and schema-bound measures/dimensions. RelOp only; no SQL.",
        "inputSchema": {"type": "object", "properties": {"workdir": _WORKDIR, "dsn": _DSN}},
    },
    {
        "name": "rr_analytics_scaffold",
        "description": "Bind a named analytics recipe to the schema and write a RelOp plan. Does not execute. Roll out on the dummy sandbox next.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe": {"type": "string"},
                "measure": {"type": "string"},
                "dimension": {"type": "string"},
                "dimension2": {"type": "string"},
                "value": {"type": "string"},
                "year": {"type": "string"},
                "n": {"type": "integer"},
                "threshold": {"type": "number"},
                "min": {"type": "number"},
                "left": {"type": "string"},
                "right": {"type": "string"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
            "required": ["recipe"],
        },
    },
    {
        "name": "rr_analytics_rollout",
        "description": "Execute a scaffolded analytics RelOp on the local dummy duplicate only. Saves the validation ticket required for promote.",
        "inputSchema": {
            "type": "object",
            "properties": {"plan": {"type": "string"}, "dsn": _DSN, "workdir": _WORKDIR},
            "required": ["plan"],
        },
    },
    {
        "name": "rr_analytics_promote",
        "description": "Replay a rolled-out analytics plan against live. Blocked until dummy-sandbox validation is saved.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan": {"type": "string"},
                "allow_live": {"type": "boolean"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
            "required": ["plan"],
        },
    },
    {
        "name": "rr_analytics_primitives",
        "description": "List the analytics primitive taxonomy (24 families including vector RAG, Socratic intent, and RelOp ideation), named composites, and chain rules. Schema-agnostic; bind at apply time. Never SQL.",
        "inputSchema": {"type": "object", "properties": {"workdir": _WORKDIR, "dsn": _DSN}},
    },
    {
        "name": "rr_analytics_chain",
        "description": "Compose analytics primitives into a RelOp plan. Pass steps [{op, ...binds}] and/or a named composite. Optional rollout on the dummy sandbox. Never SQL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {"type": "array", "items": {"type": "object"}},
                "composite": {"type": "string"},
                "rollout": {"type": "boolean"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_rag",
        "description": "Semantic or causal RAG. RelOp knn on the dummy overlay (sandbox ticket) plus LangChain/Chroma MiniLM. The SLM never writes Chroma filters — strategy/column become metadata equality. sandboxOnly; do not promote.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Retrieve phrase"},
                "question": {"type": "string"},
                "strategy": {"type": "string", "description": "semantic or causal"},
                "column": {"type": "string"},
                "n": {"type": "integer"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_causal",
        "description": "Causal RelOp plan: SLM CausalPlan (primitive IDs + binds) then sandbox pair/attach/intervene/vs_world. Never SQL or Chroma filters. sandboxOnly; do not promote.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "query": {"type": "string"},
                "column": {"type": "string"},
                "n": {"type": "integer"},
                "explore": {"type": "boolean", "description": "If true, enumerate legal causal RelOps and pick the dummy winner"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_causal_explore",
        "description": "Goal-scored abduce over legal causal RelOps. Runs each composite on the dummy, scores vs Goal, writes AskLog, returns the winner. Never SQL or Chroma filters. sandboxOnly; do not promote.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "query": {"type": "string"},
                "column": {"type": "string"},
                "n": {"type": "integer"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_causal_heuristic",
        "description": "Bind a because-clause to schema measures, search treatments, validate with a GLM odds-ratio on dummy fact RelOp, then replay on live. Overlay is not promoted. Heuristic evidence, not identification.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "query": {"type": "string"},
                "live": {"type": "boolean"},
                "discourse": {"type": "boolean"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_pearl",
        "description": "Pearl backdoor identification on the declared DAG, GLM odds-ratio and do() CASE on dummy facts, then the same RelOps on live Superstore. Overlay is not promoted. CASE is SELECT, not UPDATE.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "query": {"type": "string"},
                "live": {"type": "boolean"},
                "discourse": {"type": "boolean"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_chroma",
        "description": "Dummy Chroma overlay status or resync from OverlayChunk. Local MiniLM only. Never copies live PII.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "status (default) or sync"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_automine",
        "description": "Mine, causal-reflect, splice live cues/genes into the next ask, pivot column/family, re-cause, expand catalogued follow-ons, then draft a citation-grounded report. Never SQL. Not a discovery claim.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "query": {"type": "string"},
                "passes": {"type": "integer", "description": "Max loop passes (default 3, hard max 8)"},
                "untilStable": {"type": "boolean"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_report",
        "description": "Draft or reload a citation-grounded research report from automine findings. Planner → researcher → reporter → validator. Cites RelOp pairs, catalog NCBI/UniProt accessions, and KPI rows only. Local SLM or cloud API if provided. Never invent papers. Not conclusive proof.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "If no automine.json exists, run automine first"},
                "query": {"type": "string"},
                "rerun": {"type": "boolean", "description": "Ignore saved automine.json and mine again"},
                "passes": {"type": "integer"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
        },
    },
    {
        "name": "rr_kpi",
        "description": "Run a bound domain KPI recipe on dummy, then the same RelOp on live. Never SQL.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kpi": {"type": "string", "description": "cases_by_gene, share_of_cases, …"},
                "id": {"type": "string"},
                "live": {"type": "boolean"},
                "dsn": _DSN,
                "workdir": _WORKDIR,
            },
            "required": ["kpi"],
        },
    },
    {
        "name": "rr_gene",
        "description": "Write the public NCBI FASTA / pineoblastoma gene sqlite sample.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dest": {"type": "string"},
                "workdir": _WORKDIR,
            },
        },
    },
]

_DISPATCH = {
    "rr_install": tool_install,
    "rr_boot": tool_boot,
    "rr_question": tool_question,
    "rr_health": tool_health,
    "rr_connect": tool_connect,
    "rr_build": tool_build,
    "rr_schema": tool_schema,
    "rr_policy": tool_policy,
    "rr_ask": tool_ask,
    "rr_compile": tool_compile,
    "rr_validate": tool_validate,
    "rr_sandbox": tool_sandbox,
    "rr_promote": tool_promote,
    "rr_engines": tool_engines,
    "rr_slm": tool_slm,
    "rr_analytics_list": tool_analytics_list,
    "rr_analytics_scaffold": tool_analytics_scaffold,
    "rr_analytics_rollout": tool_analytics_rollout,
    "rr_analytics_promote": tool_analytics_promote,
    "rr_analytics_primitives": tool_analytics_primitives,
    "rr_analytics_chain": tool_analytics_chain,
    "rr_rag": tool_rag,
    "rr_causal": tool_causal,
    "rr_causal_explore": tool_causal_explore,
    "rr_causal_heuristic": tool_causal_heuristic,
    "rr_pearl": tool_pearl,
    "rr_chroma": tool_chroma,
    "rr_automine": tool_automine,
    "rr_report": tool_report,
    "rr_kpi": tool_kpi,
    "rr_gene": tool_gene,
}


def dispatch(name: str, arguments: dict[str, Any] | None = None) -> Any:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool: {name}", "tools": [t["name"] for t in MCP_TOOLS]}
    return fn(dict(arguments or {}))


def list_resources() -> list[dict[str, Any]]:
    workdir = Path(os.environ.get("REVOLVERELATE_WORKDIR") or ".").resolve()
    return [
        {
            "uri": "revolverelate://instructions",
            "name": "Agent instructions",
            "mimeType": "text/plain",
            "description": "How to boot any DSN and ask any question. Never SQL.",
        },
        {
            "uri": "revolverelate://install",
            "name": "MCP host install",
            "mimeType": "application/json",
            "description": "Cursor, VS Code, Claude, Windsurf, generic mcp.json snippets.",
        },
        {
            "uri": "revolverelate://primitives",
            "name": "Analytics primitives",
            "mimeType": "application/json",
            "description": "19-family RelOp taxonomy and named composites.",
        },
        {
            "uri": "revolverelate://composites",
            "name": "Composite chain rules",
            "mimeType": "application/json",
            "description": "Phase order, depth limits, legal/illegal chains.",
        },
        {
            "uri": "revolverelate://engines",
            "name": "Engine catalog",
            "mimeType": "application/json",
            "description": "Every catalogued DSN family (compile-all, execute by tier).",
        },
        {
            "uri": f"revolverelate://build?root={workdir}",
            "name": "Build cache",
            "mimeType": "application/json",
            "description": "Saved build.json. Live push is illegal until status=complete.",
        },
        {
            "uri": f"revolverelate://graph?root={workdir}",
            "name": "Schema graph",
            "mimeType": "application/json",
            "description": "Cached .rrgraph.json primitives.",
        },
        {
            "uri": "revolverelate://rag",
            "name": "Vector RAG",
            "mimeType": "application/json",
            "description": "Dummy Chroma overlay + semantic/causal RelOp retrieve. Never SQL.",
        },
    ]


def read_resource(uri: str) -> dict[str, Any]:
    raw = str(uri or "")
    workdir = Path(os.environ.get("REVOLVERELATE_WORKDIR") or ".").resolve()
    cache = BuildCache(workdir)
    if raw.startswith("revolverelate://instructions"):
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": INSTRUCTIONS}]}
    if raw.startswith("revolverelate://install"):
        text = json.dumps(host_configs(), indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
    if raw.startswith("revolverelate://primitives"):
        from revolverelate.analytics.primitives import list_composites, list_families, list_primitives

        text = json.dumps(
            {"families": list_families(), "primitives": list_primitives(), "composites": list_composites()},
            indent=2,
        )
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
    if raw.startswith("revolverelate://composites"):
        from revolverelate.analytics.composites import load_composite_rules

        text = json.dumps(load_composite_rules(), indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
    if raw.startswith("revolverelate://engines"):
        text = json.dumps(tool_engines({}), indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
    if raw.startswith("revolverelate://build"):
        text = json.dumps(cache.load() or {"status": "missing"}, indent=2)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
    if raw.startswith("revolverelate://rag"):
        from revolverelate.vector.chroma_store import chroma_available, chroma_status

        text = json.dumps(
            {"available": chroma_available(), "status": chroma_status(workdir), "spec": "spec/vector-rag.json"},
            indent=2,
            default=str,
        )
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
    if raw.startswith("revolverelate://graph"):
        if cache.graph_path.exists():
            text = cache.graph_path.read_text(encoding="utf-8")
        else:
            text = json.dumps({"error": "no graph; call rr_build"})
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
    return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "unknown resource"}]}


def list_prompts() -> list[dict[str, Any]]:
    return [
        {
            "name": "rr_loop",
            "description": "Boot any DSN, ask any question on the dummy sandbox, promote only after validation.",
            "arguments": [
                {"name": "dsn", "description": "Database DSN (any catalogued engine)", "required": False},
                {"name": "question", "description": "Natural-language query", "required": False},
            ],
        },
        {
            "name": "rr_any_db",
            "description": "Link a new database: rr_boot then list measures and dimensions.",
            "arguments": [{"name": "dsn", "description": "Database DSN", "required": True}],
        },
        {
            "name": "rr_any_question",
            "description": "Ask a business question after the DSN is linked (auto-boot if needed).",
            "arguments": [
                {"name": "dsn", "description": "Database DSN", "required": False},
                {"name": "question", "description": "Business question", "required": True},
            ],
        },
        {
            "name": "rr_rag",
            "description": "Retrieve with semantic or causal chunks via RelOp + dummy Chroma MiniLM.",
            "arguments": [
                {"name": "query", "description": "Retrieve phrase", "required": True},
                {"name": "strategy", "description": "semantic or causal", "required": False},
            ],
        },
        {
            "name": "rr_causal",
            "description": "Causal RelOp plan: pair, attach, intervene, vs_world. Never SQL.",
            "arguments": [
                {"name": "question", "description": "Why / what-if question", "required": True},
            ],
        },
        {
            "name": "rr_causal_explore",
            "description": "Enumerate causal RelOps, sandbox-score vs Goal, keep the winner. Never SQL.",
            "arguments": [
                {"name": "question", "description": "Why / what-if question", "required": True},
            ],
        },
        {
            "name": "rr_pearl",
            "description": "Pearl backdoor + live GLM + live do() CASE. Never invent SQL.",
            "arguments": [
                {"name": "question", "description": "Why / what-if question", "required": True},
            ],
        },
        {
            "name": "rr_automine",
            "description": "Mine corpus, RelOp-reflect, expand catalogued follow-ons, then draft a cited report.",
            "arguments": [
                {"name": "dsn", "description": "Catalogued DSN / gene sqlite", "required": False},
                {"name": "question", "description": "Causal or KPI question to reflect on", "required": False},
                {"name": "passes", "description": "Max passes (default 3)", "required": False},
            ],
        },
        {
            "name": "rr_report",
            "description": "Draft a citation-grounded report from automine findings (local SLM or cloud API).",
            "arguments": [
                {"name": "dsn", "description": "Catalogued DSN / gene sqlite", "required": False},
                {"name": "question", "description": "Question if automine has not been saved yet", "required": False},
            ],
        },
    ]


def get_prompt(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = dict(arguments or {})
    text = INSTRUCTIONS
    if name == "rr_any_db":
        text += "\nLink this database with rr_boot, then list measures and dimensions. Do not invent SQL."
    if name == "rr_any_question":
        text += "\nAsk with rr_question. Auto-boot if dsn is present. RelOp only."
    if name == "rr_rag":
        text += "\nCall rr_rag. RelOp overlay knn plus dummy Chroma MiniLM. Do not write Chroma filters or SQL."
    if name == "rr_causal":
        text += "\nCall rr_causal. Emit a CausalPlan of primitive ids only. Do not write SQL or Chroma filters."
    if name == "rr_causal_explore":
        text += "\nCall rr_causal_explore. Rank legal causal composites on the dummy. Do not write SQL."
    if name == "rr_pearl":
        text += "\nCall rr_pearl. Backdoor identify, dummy GLM and CASE, then live replay. Do not write SQL."
    if name == "rr_automine":
        text += "\nCall rr_automine. RelOp reflect, expand only spec follow-ons, rebuild, mine again, then draft the cited report. Do not write SQL."
    if name == "rr_report":
        text += "\nCall rr_report after rr_automine. Cite only RelOp / catalog / KPI cards. Do not invent papers or SQL."
    if args.get("dsn"):
        text += f"\n\nDSN: {args['dsn']}"
    if args.get("question"):
        text += (
            f"\nQuestion: {args['question']}\n"
            "Call rr_question with this question (and dsn if not booted). Do not write SQL."
        )
    if args.get("query"):
        text += (
            f"\nRetrieve: {args['query']}\n"
            f"Strategy: {args.get('strategy') or 'semantic'}\n"
            "Call rr_rag. Do not invent Chroma where-filters."
        )
    return {
        "description": "RevolveRelate agent loop",
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


def handle_jsonl(message: dict[str, Any]) -> dict[str, Any]:
    method = message.get("method") or message.get("action")
    if method in {"tools/list", "list_tools", "schemas"}:
        return {"tools": MCP_TOOLS}
    if method in {"tools/call", "call"}:
        name = message.get("name") or (message.get("params") or {}).get("name")
        args = message.get("arguments") or (message.get("params") or {}).get("arguments") or {}
        if not name:
            return {"error": "tool name required"}
        return {"result": dispatch(str(name), dict(args))}
    if method == "resources/list":
        return {"resources": list_resources()}
    if method == "resources/read":
        return read_resource(str((message.get("params") or message).get("uri") or ""))
    if method == "prompts/list":
        return {"prompts": list_prompts()}
    if method == "prompts/get":
        params = message.get("params") or message
        return get_prompt(str(params.get("name") or ""), dict(params.get("arguments") or {}))
    return {"error": f"unknown method: {method}"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RevolveRelate MCP server")
    p.add_argument("--jsonl", action="store_true", help="Newline JSON instead of Content-Length framing")
    p.add_argument("--install", action="store_true", help="Print host MCP install JSON and exit")
    args = p.parse_args(argv)
    if args.install:
        print(json.dumps(host_configs(), indent=2))
        return 0
    if args.jsonl:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                print(json.dumps({"error": f"invalid json: {exc}"}), flush=True)
                continue
            print(json.dumps(handle_jsonl(msg), default=str), flush=True)
        return 0
    return run_mcp_loop(
        server_name="revolverelate",
        server_version=__version__,
        instructions=INSTRUCTIONS,
        list_tools=lambda: MCP_TOOLS,
        call_tool=dispatch,
        list_resources=list_resources,
        read_resource=read_resource,
        list_prompts=list_prompts,
        get_prompt=get_prompt,
    )


if __name__ == "__main__":
    raise SystemExit(main())
