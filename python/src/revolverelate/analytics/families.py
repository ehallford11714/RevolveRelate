"""Apply branches for extended analytics families. RelOp only."""

from __future__ import annotations

from revolverelate.analytics.bind import list_measures, pick_dimension, pick_measure, resolve_column
from revolverelate.ir.rel import (
    agg,
    agg_fn,
    attr_ref,
    binop,
    case,
    col,
    col_item,
    count_star,
    distinct,
    filt,
    fn,
    item,
    lim,
    lit,
    over,
    project,
    scan,
    sort,
)


def _cte_scan(op: dict | None, graph) -> bool:
    return bool(op and op.get("op") == "scan" and graph.entity(op.get("entity") or "") is None)


def _grouped(op: dict | None) -> bool:
    current = op
    while current:
        if current.get("op") == "aggregate":
            return True
        current = current.get("input") or current.get("left")
    return False


def _reduced(op: dict | None, graph) -> bool:
    return _grouped(op) or _cte_scan(op, graph)


def apply_extended(graph, spec: dict, op: dict | None, args: dict, apply, ensure, measure, dim, date) -> dict:
    family = spec["family"]
    dispatch = {
        "compare": _compare,
        "stat": _stat,
        "quality": _quality,
        "shape": _shape,
        "calendar": _calendar,
        "sequence": _sequence,
        "hierarchy": _hierarchy,
        "nested": _nested,
        "match": _match,
        "chunk": _chunk,
        "vector": _vector,
        "intent": _intent,
        "world": _world,
        "search": _search,
    }
    fn_ = dispatch.get(family)
    if not fn_:
        raise KeyError(family)
    return fn_(graph, spec, op, args, apply, ensure, measure, dim, date)


def _compare(graph, spec, op, args, apply, ensure, measure, dim, date):
    pid = spec["id"]
    if pid == "vs_world":
        from revolverelate.analytics.intent_apply import apply_vs_world

        return apply_vs_world(graph, spec, op, args, ensure, measure, dim)
    if pid == "vs_target":
        m = measure(graph, args)
        src = ensure(graph, op, m)
        raw = args.get("threshold")
        if raw is None:
            raw = args.get("n") if args.get("n") is not None else 0
        try:
            target = float(raw)
        except (TypeError, ValueError):
            target = 0.0
        return project(
            src,
            col_item(m.entity_name, m.attr_name, "value"),
            item(lit(target), "baseline"),
            item(binop("-", col(m.entity_name, m.attr_name), lit(target)), "delta"),
        )
    if pid == "index_100":
        m, d = measure(graph, args), date(graph, args)
        src = ensure(graph, op, m, d)
        order = [{"expr": col(d.entity_name, d.attr_name), "direction": "ASC"}] if d else [{"expr": col(m.entity_name, m.attr_name), "direction": "ASC"}]
        return project(
            src,
            col_item(m.entity_name, m.attr_name, "value"),
            item(over("first_value", inp=col(m.entity_name, m.attr_name), order=order), "baseline"),
        )
    if pid in {"vs_peer", "contribution"}:
        if not _reduced(op, graph):
            op = apply(graph, "agg_sum_by", op, args)
        d = dim(graph, args)
        src = op
        total = over("sum", inp=attr_ref("value")) if pid == "contribution" else over("avg", inp=attr_ref("value"))
        alias = "total" if pid == "contribution" else "peer"
        return project(src, item(attr_ref(d.attr_name), d.attr_name), item(attr_ref("value"), "value"), item(total, alias))
    # vs_prior / growth_pct
    if not _reduced(op, graph):
        m, d, dt = measure(graph, args), dim(graph, args), date(graph, args)
        src = ensure(graph, op, m, d, dt)
        year = fn("substr", col(dt.entity_name, dt.attr_name), lit(1), lit(4)) if dt else col(d.entity_name, d.attr_name)
        src = project(
            src,
            col_item(d.entity_name, d.attr_name, d.attr_name),
            item(year, "grain"),
            col_item(m.entity_name, m.attr_name, "raw"),
        )
        # cannot agg on projected aliases easily — group by dim + date year via like years instead
        op = apply(graph, "agg_sum_by", op, args)
    d = dim(graph, args)
    lagged = project(
        op,
        item(attr_ref(d.attr_name), d.attr_name),
        item(attr_ref("value"), "value"),
        item(over("lag", inp=attr_ref("value"), order=[{"expr": attr_ref("value"), "direction": "ASC"}]), "prior"),
    )
    if pid == "growth_pct":
        return project(
            lagged,
            item(attr_ref(d.attr_name), d.attr_name),
            item(attr_ref("value"), "value"),
            item(attr_ref("prior"), "prior"),
            item(binop("/", binop("-", attr_ref("value"), attr_ref("prior")), fn("nullif", attr_ref("prior"), lit(0))), "growth"),
        )
    return lagged


def _stat(graph, spec, op, args, apply, ensure, measure, dim, date):
    pid = spec["id"]
    m = measure(graph, args)
    src = ensure(graph, op, m)
    if pid in {"stddev", "variance"}:
        fn_name = "stddev_samp" if pid == "stddev" else "var_samp"
        return agg(src, [], agg_fn(fn_name, col(m.entity_name, m.attr_name), "value"))
    if pid == "corr":
        b = pick_measure(graph, args.get("measure2"))
        src = ensure(graph, op, m, b)
        return project(src, col_item(m.entity_name, m.attr_name, "value"), col_item(b.entity_name, b.attr_name, "other"))
    if pid == "zscore":
        return project(
            src,
            col_item(m.entity_name, m.attr_name, "value"),
            item(over("avg", inp=col(m.entity_name, m.attr_name)), "mean"),
            item(binop("-", col(m.entity_name, m.attr_name), over("avg", inp=col(m.entity_name, m.attr_name))), "z"),
        )
    if pid == "iqr":
        tiled = project(
            src,
            col_item(m.entity_name, m.attr_name, "value"),
            item(over("ntile", inp=lit(4), order=[{"expr": col(m.entity_name, m.attr_name), "direction": "ASC"}]), "bucket"),
        )
        return tiled
    # median / pctl: window rank then filter
    ranked = project(
        src,
        col_item(m.entity_name, m.attr_name, "value"),
        item(over("percent_rank", order=[{"expr": col(m.entity_name, m.attr_name), "direction": "ASC"}]), "p"),
        item(over("row_number", order=[{"expr": col(m.entity_name, m.attr_name), "direction": "ASC"}]), "rk"),
        item(over("count", inp={"expr": "star"}), "n"),
    )
    if pid == "pctl":
        return lim(filt(ranked, binop(">=", attr_ref("p"), lit(float(args.get("p") or 0.9)))), 1)
    return lim(filt(ranked, binop(">=", attr_ref("p"), lit(0.5))), 1)


def _quality(graph, spec, op, args, apply, ensure, measure, dim, date):
    pid = spec["id"]
    if pid == "volume":
        return agg(ensure(graph, op), [], count_star("value"))
    if pid == "freshness":
        d = date(graph, args)
        if not d:
            return apply(graph, "agg_max", op, args)
        return agg(ensure(graph, op, d), [], agg_fn("max", col(d.entity_name, d.attr_name), "value"))
    bound = resolve_column(graph, args.get("column") or args.get("measure") or pick_measure(graph).attr_name)
    src = ensure(graph, op, bound)
    if pid == "distinct_rate":
        grouped = agg(
            src,
            [],
            count_star("n"),
            item({"expr": "agg", "fn": "count", "distinct": True, "input": col(bound.entity_name, bound.attr_name)}, "filled"),
        )
        return project(grouped, item(binop("/", attr_ref("filled"), attr_ref("n")), "value"))
    grouped = agg(src, [], count_star("n"), agg_fn("count", col(bound.entity_name, bound.attr_name), "filled"))
    if pid == "complete_rate":
        return project(grouped, item(binop("/", attr_ref("filled"), attr_ref("n")), "value"))
    return project(grouped, item(binop("/", binop("-", attr_ref("n"), attr_ref("filled")), attr_ref("n")), "value"))


def _shape(graph, spec, op, args, apply, ensure, measure, dim, date):
    pid = spec["id"]
    if pid in {"stack", "unpivot"}:
        a, b = measure(graph, args), pick_measure(graph, args.get("measure2"))
        src = ensure(graph, op, a, b)
        left = project(src, item(lit(a.attr_name), "name"), col_item(a.entity_name, a.attr_name, "value"))
        right = project(src, item(lit(b.attr_name), "name"), col_item(b.entity_name, b.attr_name, "value"))
        return {"op": "setop", "set": "union", "all": True, "left": left, "right": right}
    if pid in {"widen", "pivot"}:
        d, m = dim(graph, args), measure(graph, args)
        src = ensure(graph, op, d, m)
        samples = list(d.attr.samples[:3] or ["A", "B"])
        items = []
        for sample in samples:
            items.append(
                item(
                    case(
                        [{"when": binop("=", col(d.entity_name, d.attr_name), lit(sample)), "then": col(m.entity_name, m.attr_name)}],
                        lit(0),
                    ),
                    str(sample).replace(" ", "_"),
                )
            )
        return project(src, *items)
    # first_row_per
    d, dt = dim(graph, args), date(graph, args)
    m = None
    try:
        m = measure(graph, args)
    except Exception:
        m = None
    src = ensure(graph, op, d, dt, m)
    direction = (args.get("direction") or "DESC").upper()
    if direction not in {"ASC", "DESC"}:
        direction = "DESC"
    order = [{"expr": col(dt.entity_name, dt.attr_name), "direction": direction}] if dt else [{"expr": col(d.entity_name, d.attr_name), "direction": "ASC"}]
    items = [col_item(d.entity_name, d.attr_name, d.attr_name)]
    for name in list_measures(graph):
        try:
            bound = pick_measure(graph, name)
        except Exception:
            continue
        items.append(col_item(bound.entity_name, bound.attr_name, bound.attr_name))
    items.append(item(over("row_number", partition=[col(d.entity_name, d.attr_name)], order=order), "rk"))
    ranked = project(src, *items)
    return filt(ranked, binop("=", attr_ref("rk"), lit(1)))


def _calendar(graph, spec, op, args, apply, ensure, measure, dim, date):
    pid = spec["id"]
    d = date(graph, args)
    src = ensure(graph, op, d) if d else ensure(graph, op)
    if not d:
        return src
    c = col(d.entity_name, d.attr_name)
    if pid == "fiscal_year":
        return project(src, item(fn("substr", c, lit(1), lit(4)), "value"))
    if pid == "workdays":
        return project(src, item(fn("julianday", c), "value"))
    if pid == "date_add":
        return project(src, item(fn("date", c, lit("+1 year")), "value"))
    return filt(src, binop("like", c, lit("%-12-25")))


def _sequence(graph, spec, op, args, apply, ensure, measure, dim, date):
    pid = spec["id"]
    d = dim(graph, args)
    if pid == "repeat":
        src = ensure(graph, op, d)
        grouped = agg(src, [col(d.entity_name, d.attr_name)], count_star("value"))
        return filt(grouped, binop(">", attr_ref("value"), lit(1)))
    dt = date(graph, args)
    src = ensure(graph, op, d, dt)
    if pid == "first_touch":
        return apply(graph, "first_row_per", op, {**args, "direction": "ASC"})
    if not dt:
        return src
    part = [col(d.entity_name, d.attr_name)]
    order = [{"expr": col(dt.entity_name, dt.attr_name), "direction": "ASC"}]
    if pid == "next_event":
        return project(src, col_item(d.entity_name, d.attr_name, d.attr_name), col_item(dt.entity_name, dt.attr_name, "value"), item(over("lead", inp=col(dt.entity_name, dt.attr_name), partition=part, order=order), "next"))
    if pid == "gap":
        return project(src, col_item(d.entity_name, d.attr_name, d.attr_name), item(over("lag", inp=col(dt.entity_name, dt.attr_name), partition=part, order=order), "prior"), col_item(dt.entity_name, dt.attr_name, "value"))
    return project(src, col_item(d.entity_name, d.attr_name, d.attr_name), item(over("row_number", partition=part, order=order), "rk"))


def _hierarchy(graph, spec, op, args, apply, ensure, measure, dim, date):
    pid = spec["id"]
    if pid == "ancestors":
        d = dim(graph, args)
        return distinct(project(scan(d.entity_name), col_item(d.entity_name, d.attr_name, d.attr_name)))
    if pid in {"descendants", "leaf"}:
        key = "dimension2" if pid == "descendants" else "dimension"
        d = pick_dimension(graph, args.get(key) or args.get("dimension"))
        return distinct(project(scan(d.entity_name), col_item(d.entity_name, d.attr_name, d.attr_name)))
    grouped = apply(graph, "agg_sum_by", op, args)
    d = dim(graph, args)
    left = project(grouped, item(attr_ref(d.attr_name), "grain"), item(attr_ref("value"), "value"))
    total = apply(graph, "agg_sum", op, args)
    right = project(total, item(lit("ALL"), "grain"), item(attr_ref("value"), "value"))
    if pid == "rollup":
        return {"op": "setop", "set": "union", "all": True, "left": left, "right": right}
    d2 = pick_dimension(graph, args.get("dimension2"), avoid=d)
    both = apply(graph, "agg_sum_by", op, {**args, "dimension": d.attr_name})
    mid = apply(graph, "agg_sum_by", op, {**args, "dimension": d2.attr_name})
    a = project(both, item(attr_ref(d.attr_name), "grain"), item(attr_ref("value"), "value"))
    b = project(mid, item(attr_ref(d2.attr_name), "grain"), item(attr_ref("value"), "value"))
    return {"op": "setop", "set": "union", "all": True, "left": {"op": "setop", "set": "union", "all": True, "left": a, "right": b}, "right": right}


def _nested(graph, spec, op, args, apply, ensure, measure, dim, date):
    from revolverelate.analytics.vector_apply import apply_nested_real

    return apply_nested_real(graph, spec, op, args, ensure, dim)


def _chunk(graph, spec, op, args, apply, ensure, measure, dim, date):
    from revolverelate.analytics.vector_apply import apply_chunk

    return apply_chunk(graph, spec, op, args)


def _vector(graph, spec, op, args, apply, ensure, measure, dim, date):
    from revolverelate.analytics.vector_apply import apply_vector

    return apply_vector(graph, spec, op, args)


def _intent(graph, spec, op, args, apply, ensure, measure, dim, date):
    from revolverelate.analytics.intent_apply import apply_intent

    return apply_intent(graph, spec, op, args, ensure, measure, dim)


def _world(graph, spec, op, args, apply, ensure, measure, dim, date):
    from revolverelate.analytics.intent_apply import apply_world

    return apply_world(graph, spec, op, args, ensure, measure, dim)


def _search(graph, spec, op, args, apply, ensure, measure, dim, date):
    from revolverelate.analytics.intent_apply import apply_search

    return apply_search(graph, spec, op, args, ensure, measure, dim)


def _match(graph, spec, op, args, apply, ensure, measure, dim, date):
    pid = spec["id"]
    if pid == "fuzzy_eq":
        return apply(graph, "prefix_like", op, args)
    if pid == "nearest":
        m = measure(graph, args)
        src = ensure(graph, op, m)
        target = float(args.get("threshold") or 0)
        ranked = project(
            src,
            col_item(m.entity_name, m.attr_name, "value"),
            item(fn("abs", binop("-", col(m.entity_name, m.attr_name), lit(target))), "dist"),
            item(over("rank", order=[{"expr": fn("abs", binop("-", col(m.entity_name, m.attr_name), lit(target))), "direction": "ASC"}]), "rk"),
        )
        return filt(ranked, binop("=", attr_ref("rk"), lit(1)))
    m, d, dt = measure(graph, args), dim(graph, args), date(graph, args)
    src = ensure(graph, op, m, d, dt)
    order = [{"expr": col(dt.entity_name, dt.attr_name), "direction": "ASC"}] if dt else [{"expr": col(m.entity_name, m.attr_name), "direction": "ASC"}]
    part = [col(d.entity_name, d.attr_name)]
    return project(
        src,
        col_item(d.entity_name, d.attr_name, d.attr_name),
        col_item(m.entity_name, m.attr_name, "value"),
        item(over("last_value", inp=col(m.entity_name, m.attr_name), partition=part, order=order), "asof"),
    )
