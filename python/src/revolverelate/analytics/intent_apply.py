"""Apply intent, world, and search primitives. RelOp only — no new IR kinds."""

from __future__ import annotations

import json

from revolverelate.analytics.asklog import ASKLOG
from revolverelate.analytics.bind import pick_fact, resolve_column
from revolverelate.catalog import spec_dir
from revolverelate.ir.rel import (
    agg,
    agg_fn,
    attr_ref,
    binop,
    case,
    col,
    col_item,
    filt,
    item,
    join,
    lit,
    project,
    scan,
)
from revolverelate.vector.overlay import OVERLAY


def load_intent_spec() -> dict:
    return json.loads((spec_dir() / "intent-explore.json").read_text(encoding="utf-8"))


def match_templates(objective: str) -> list[dict]:
    text = (objective or "").casefold()
    hits = []
    for row in load_intent_spec().get("socratic", {}).get("templates") or []:
        if any(token in text for token in row.get("match") or []):
            hits.append(row)
    if not hits:
        hits = list(load_intent_spec().get("socratic", {}).get("templates") or [])[:3]
    return hits


def match_composite(objective: str) -> str:
    hits = match_templates(objective)
    return str(hits[0]["composite"]) if hits else "west_sales_by_category"


def ideate_candidates(objective: str | None = None) -> list[str]:
    listed = list(load_intent_spec().get("ideate", {}).get("candidates") or [])
    if not objective:
        return listed
    hits = [str(t["composite"]) for t in match_templates(objective)]
    out = []
    for cid in hits + listed:
        if cid not in out:
            out.append(cid)
    return out


def _values(columns: list[str], rows: list[list], alias: str) -> dict:
    return {"op": "values", "columns": columns, "rows": rows or [[""] * len(columns)], "alias": alias}


def apply_intent(graph, spec, op, args, ensure, measure, dim):
    pid = spec["id"]
    objective = str(args.get("query") or args.get("value") or "west sales")
    if pid == "socratic":
        rows = [[i + 1, t["question"], t["composite"]] for i, t in enumerate(match_templates(objective))]
        return _values(["ordinal", "question", "composite"], rows, "socratic")
    if pid == "objective":
        src = ensure(graph, op)
        return project(src, item(lit(objective), "objective"))
    if pid == "goal":
        m = measure(graph, args)
        src = ensure(graph, op, m)
        raw = args.get("threshold")
        try:
            target = float(0 if raw is None else raw)
        except (TypeError, ValueError):
            target = 0.0
        return project(
            src,
            col_item(m.entity_name, m.attr_name, "value"),
            item(lit(target), "target"),
            item(fn_abs(binop("-", col(m.entity_name, m.attr_name), lit(target))), "utility"),
        )
    return ensure(graph, op)


def fn_abs(expr: dict) -> dict:
    from revolverelate.ir.rel import fn

    return fn("abs", expr)


def apply_world(graph, spec, op, args, ensure, measure, dim):
    pid = spec["id"]
    if pid == "ask_log":
        if graph.entity(ASKLOG) is None:
            return _values(["AskId", "Question"], [[0, ""]], "asklog")
        return scan(ASKLOG)
    if pid == "hypothesize":
        inner = ensure(graph, op)
        name = str(args.get("name") or "Hypothesis")
        if inner.get("op") == "with":
            return {
                "op": "with",
                "ctes": list(inner.get("ctes") or []) + [{"name": name, "input": inner.get("input")}],
                "input": scan(name),
            }
        return {"op": "with", "ctes": [{"name": name, "input": inner}], "input": scan(name)}
    if pid == "intervene":
        m = measure(graph, args)
        d = dim(graph, args)
        try:
            slice_col = resolve_column(graph, args.get("column") or d.attr_name)
        except Exception:
            slice_col = d
        src = ensure(graph, op, m, d, slice_col)
        try:
            rewritten = float(args.get("threshold") if args.get("threshold") is not None else 0)
        except (TypeError, ValueError):
            rewritten = 0.0
        pred = binop("=", col(slice_col.entity_name, slice_col.attr_name), lit(args.get("value") or "West"))
        return project(
            src,
            col_item(d.entity_name, d.attr_name, d.attr_name),
            col_item(m.entity_name, m.attr_name, "observed"),
            item(case([{"when": pred, "then": lit(rewritten)}], col(m.entity_name, m.attr_name)), "intervened"),
        )
    return ensure(graph, op)


def apply_search(graph, spec, op, args, ensure, measure, dim):
    pid = spec["id"]
    objective = str(args.get("query") or args.get("value") or "west sales")
    if pid == "ideate":
        rows = [[i + 1, cid] for i, cid in enumerate(ideate_candidates(objective))]
        return _values(["ordinal", "composite"], rows, "ideate")
    if pid == "explore":
        quests = match_templates(objective)
        rows = [[i + 1, t["question"], t["composite"], "socratic"] for i, t in enumerate(quests)]
        for i, cid in enumerate(ideate_candidates(objective), start=len(rows) + 1):
            rows.append([i, cid, cid, "ideate"])
        return _values(["ordinal", "question", "composite", "kind"], rows, "explore")
    if pid == "abduce":
        from revolverelate.analytics.primitives import chain, get_composite

        cid = match_composite(objective)
        try:
            steps = list(get_composite(cid)["steps"])
            ir = chain(graph, steps)
            return ir.get("op") or ir
        except Exception:
            return ensure(graph, op)
    return ensure(graph, op)


def apply_vs_world(graph, spec, op, args, ensure, measure, dim):
    """Sum observed vs intervened in one aggregate so CASE lits are not dropped."""
    m = measure(graph, args)
    d = dim(graph, args)
    try:
        slice_col = resolve_column(graph, args.get("column") or "Region")
    except Exception:
        slice_col = d
    base = op
    while base and base.get("op") == "project":
        base = base.get("input")
    src = ensure(graph, base, m, d, slice_col)
    try:
        rewritten = float(args.get("threshold") if args.get("threshold") is not None else 0)
    except (TypeError, ValueError):
        rewritten = 0.0
    pred = binop("=", col(slice_col.entity_name, slice_col.attr_name), lit(args.get("value") or "West"))
    intervened = case([{"when": pred, "then": lit(rewritten)}], col(m.entity_name, m.attr_name))
    return agg(
        src,
        [col(d.entity_name, d.attr_name)],
        agg_fn("sum", col(m.entity_name, m.attr_name), "observed"),
        agg_fn("sum", intervened, "intervened"),
    )


def apply_attach_fact(graph, op, args):
    fact = pick_fact(graph)
    src = op if (op and op.get("op")) else scan(OVERLAY) if graph.entity(OVERLAY) else scan(fact.name)
    if graph.entity(OVERLAY) is None:
        return src
    fks = [r for r in graph.relationships if r.from_entity.casefold() == fact.name.casefold()]
    prefer = str(args.get("entity") or "Product").casefold()
    rel = next((r for r in fks if r.to_entity.casefold() == prefer), fks[0] if fks else None)
    if rel is None:
        return src
    joined = join(
        src,
        scan(fact.name),
        binop("=", attr_ref("SourcePk"), col(fact.name, rel.from_attrs[0])),
        join_type="inner",
    )
    needed = []
    column = args.get("column")
    if column:
        try:
            needed.append(resolve_column(graph, column).entity_name)
        except Exception:
            pass
    from revolverelate.ir.rel import attach_entities

    joined = attach_entities(graph, joined, needed, root=fact.name)
    if column and args.get("value") is not None:
        try:
            bound = resolve_column(graph, column)
            joined = filt(joined, binop("=", col(bound.entity_name, bound.attr_name), lit(args.get("value"))))
        except Exception:
            pass
    return joined
