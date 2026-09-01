"""Apply chunk and vector primitives. RelOp only — scans OverlayChunk when present."""

from __future__ import annotations

from revolverelate.analytics.bind import resolve_column
from revolverelate.ir.rel import (
    attr_ref,
    binop,
    col,
    col_item,
    entities_in,
    filt,
    fn,
    item,
    join,
    lim,
    lit,
    over,
    project,
    scan,
    sort,
)
from revolverelate.vector.embed import fingerprint
from revolverelate.vector.overlay import OVERLAY, _SKIP_DIM

_CHUNK_STRATEGY = {
    "chunk_fixed": "fixed",
    "chunk_token": "token",
    "chunk_sentence": "sentence",
    "chunk_window": "window",
    "chunk_recursive": "recursive",
    "chunk_semantic": "semantic",
    "chunk_causal": "causal",
    "chunk_hier": "hier",
    "chunk_prop": "prop",
    "chunk_late": "late",
    "chunk_topic": "topic",
    "chunk_discourse": "discourse",
    "chunk_event": "event",
}


def has_overlay(graph) -> bool:
    return graph.entity(OVERLAY) is not None


def _scope(graph, op, args, strategy: str | None = None):
    column = args.get("column")
    entity = args.get("entity")
    if op and op.get("op"):
        current = op
    elif has_overlay(graph):
        current = scan(OVERLAY)
    else:
        bound = resolve_column(graph, column or "ProductName")
        return project(scan(bound.entity_name), col_item(bound.entity_name, bound.attr_name, "Text"))
    if has_overlay(graph) and OVERLAY.casefold() in entities_in(current):
        if column and column.replace("_", "").casefold() not in _SKIP_DIM:
            current = filt(current, binop("=", col(OVERLAY, "Column"), lit(column)))
        if entity:
            current = filt(current, binop("=", col(OVERLAY, "Entity"), lit(entity)))
        if strategy:
            current = filt(current, binop("=", col(OVERLAY, "Strategy"), lit(strategy)))
    return current


def apply_chunk(graph, spec, op, args):
    if spec["id"] == "overlay":
        src = _scope(graph, op, args, None)
        if not has_overlay(graph):
            return src
        return project(
            src,
            col_item(OVERLAY, "Text", "Text"),
            col_item(OVERLAY, "Strategy", "Strategy"),
            col_item(OVERLAY, "Level", "Level"),
            col_item(OVERLAY, "Hash", "Hash"),
            col_item(OVERLAY, "SourcePk", "SourcePk"),
            col_item(OVERLAY, "Entity", "Entity"),
            col_item(OVERLAY, "Column", "Column"),
            col_item(OVERLAY, "Cue", "Cue"),
            col_item(OVERLAY, "Role", "Role"),
            col_item(OVERLAY, "Score", "Score"),
            col_item(OVERLAY, "ParentId", "ParentId"),
        )
    strategy = _CHUNK_STRATEGY.get(spec["id"], spec["id"].replace("chunk_", "", 1))
    src = _scope(graph, op, args, strategy)
    if not has_overlay(graph):
        return src
    return project(
        src,
        col_item(OVERLAY, "Text", "Text"),
        col_item(OVERLAY, "Strategy", "Strategy"),
        col_item(OVERLAY, "Level", "Level"),
        col_item(OVERLAY, "Hash", "Hash"),
        col_item(OVERLAY, "SourcePk", "SourcePk"),
        col_item(OVERLAY, "Cue", "Cue"),
        col_item(OVERLAY, "Role", "Role"),
        col_item(OVERLAY, "Score", "Score"),
        col_item(OVERLAY, "ParentId", "ParentId"),
    )


def apply_vector(graph, spec, op, args):
    pid = spec["id"]
    src = _scope(graph, op, args, args.get("strategy"))
    query = str(args.get("query") or args.get("value") or "bookcase")
    n = int(args.get("n") or 5)
    qh = fingerprint(query)
    if pid == "embed":
        if not has_overlay(graph):
            return project(src, item(lit(qh), "Hash"), item(lit(query), "query"))
        return project(
            src,
            col_item(OVERLAY, "Text", "Text"),
            col_item(OVERLAY, "Vec", "Vec"),
            col_item(OVERLAY, "Hash", "Hash"),
            col_item(OVERLAY, "Model", "Model"),
            item(lit(qh), "queryHash"),
        )
    if pid == "knn" or pid == "rerank":
        if not has_overlay(graph):
            return lim(src, n)
        scored = project(
            src,
            col_item(OVERLAY, "Text", "Text"),
            col_item(OVERLAY, "SourcePk", "SourcePk"),
            col_item(OVERLAY, "Entity", "Entity"),
            col_item(OVERLAY, "Column", "Column"),
            col_item(OVERLAY, "Strategy", "Strategy"),
            col_item(OVERLAY, "Role", "Role"),
            col_item(OVERLAY, "Cue", "Cue"),
            col_item(OVERLAY, "ParentId", "ParentId"),
            col_item(OVERLAY, "Hash", "Hash"),
            item(fn("abs", binop("-", col(OVERLAY, "Hash"), lit(qh))), "dist"),
        )
        return lim(sort(scored, {"expr": attr_ref("dist"), "direction": "ASC"}), n)
    if pid == "sim_join":
        if not has_overlay(graph):
            return src
        ranked = project(
            src,
            col_item(OVERLAY, "Text", "Text"),
            col_item(OVERLAY, "SourcePk", "SourcePk"),
            col_item(OVERLAY, "Hash", "Hash"),
            item(
                over("rank", partition=[col(OVERLAY, "Column")], order=[{"expr": col(OVERLAY, "Hash"), "direction": "ASC"}]),
                "rk",
            ),
        )
        return filt(ranked, binop("<=", attr_ref("rk"), lit(n)))
    if pid == "attach_source":
        entity = str(args.get("entity") or "")
        target = graph.entity(entity) if entity else None
        if target is None:
            from revolverelate.analytics.bind import pick_fact

            target = pick_fact(graph)
        pk = target.pk_attrs()
        key = pk[0].name if pk else target.attributes[0].name
        if not has_overlay(graph):
            return scan(target.name)
        return join(
            src if (op and op.get("op")) else scan(OVERLAY),
            scan(target.name),
            binop("=", attr_ref("SourcePk"), col(target.name, key)),
            join_type="inner",
        )
    if pid == "attach_fact":
        from revolverelate.analytics.intent_apply import apply_attach_fact

        return apply_attach_fact(graph, src if (op and op.get("op")) else None, args)
    if pid == "role_is":
        return _role_is(graph, op, args)
    if pid == "filter_cue":
        return _filter_cue(graph, op, args)
    if pid == "pair_causal":
        return _pair_causal(graph, op, args)
    return src


def _role_is(graph, op, args):
    role = str(args.get("value") or args.get("role") or "cause")
    src = op if (op and op.get("op")) else _scope(graph, None, args, "causal")
    if has_overlay(graph) and OVERLAY.casefold() in entities_in(src):
        return filt(src, binop("=", col(OVERLAY, "Role"), lit(role)))
    return filt(src, binop("=", attr_ref("Role"), lit(role)))


def _filter_cue(graph, op, args):
    cue = str(args.get("value") or args.get("cue") or "because")
    src = op if (op and op.get("op")) else _scope(graph, None, args, "causal")
    if has_overlay(graph) and OVERLAY.casefold() in entities_in(src):
        return filt(src, binop("=", col(OVERLAY, "Cue"), lit(cue)))
    return filt(src, binop("=", attr_ref("Cue"), lit(cue)))


def _pair_causal(graph, op, args):
    """Cause ⋈ Effect on SourcePk and Cue. WITH CTEs — no new IR kind."""
    left_role = str(args.get("left") or args.get("value") or "cause")
    right_role = str(args.get("right") or "effect")
    inner = op if (op and op.get("op")) else _scope(graph, None, {**args, "column": args.get("column") or "ProductName"}, "causal")
    hits, cause_n, effect_n = "CausalHits", "Cause", "Effect"
    cause_tree = filt(scan(hits), binop("=", col(hits, "Role"), lit(left_role)))
    effect_tree = filt(scan(hits), binop("=", col(hits, "Role"), lit(right_role)))
    joined = join(
        scan(cause_n),
        scan(effect_n),
        binop("=", col(cause_n, "SourcePk"), col(effect_n, "SourcePk")),
        binop("=", col(cause_n, "Cue"), col(effect_n, "Cue")),
    )
    return {
        "op": "with",
        "ctes": [
            {"name": hits, "input": inner},
            {"name": cause_n, "input": cause_tree},
            {"name": effect_n, "input": effect_tree},
        ],
        "input": project(
            joined,
            col_item(cause_n, "Text", "CauseText"),
            col_item(effect_n, "Text", "EffectText"),
            col_item(cause_n, "Cue", "Cue"),
            col_item(cause_n, "SourcePk", "SourcePk"),
            col_item(cause_n, "Entity", "Entity"),
            col_item(cause_n, "Column", "Column"),
            col_item(cause_n, "Role", "CauseRole"),
            col_item(effect_n, "Role", "EffectRole"),
        ),
    }


def apply_nested_real(graph, spec, op, args, ensure, dim):
    """Richer unnest/element: overlay chunks when present, else length/substr."""
    pid = spec["id"]
    column = args.get("column")
    if pid == "unnest" and has_overlay(graph):
        return apply_chunk(graph, {"id": "chunk_sentence"}, op, {**args, "column": column})
    bound = resolve_column(graph, column or dim(graph, args).attr_name)
    src = ensure(graph, op, bound)
    c = col(bound.entity_name, bound.attr_name)
    if pid == "element":
        return project(src, item(fn("substr", c, lit(1), lit(int(args.get("n") or 8))), "value"))
    if pid == "nested_len":
        return project(src, item(fn("length", c), "value"))
    if pid == "nested_keys":
        from revolverelate.ir.rel import distinct

        return distinct(project(scan(bound.entity_name), col_item(bound.entity_name, bound.attr_name, bound.attr_name)))
    return project(src, col_item(bound.entity_name, bound.attr_name, "value"))
