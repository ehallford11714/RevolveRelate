"""Apply and chain analytics primitives from spec/analytics-primitives.json.

Each primitive is RelOp in / RelOp out. Bind names to SchemaGraph at apply time.
Never emits SQL. Add a row to the spec to grow a family later.
"""

from __future__ import annotations

import json
from functools import lru_cache

from revolverelate.analytics.bind import (
    list_measures,
    pick_date,
    pick_dimension,
    pick_fact,
    pick_measure,
    resolve_column,
)
from revolverelate.catalog import spec_dir
from revolverelate.errors import AskError
from revolverelate.analytics.families import apply_extended
from revolverelate.ir.rel import (
    agg,
    agg_fn,
    attach_entities,
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
    join_entities,
    lim,
    lit,
    over,
    project,
    query,
    scan,
    sort,
)
from revolverelate.schema.model import SchemaGraph


@lru_cache(maxsize=1)
def load_taxonomy() -> dict:
    return json.loads((spec_dir() / "analytics-primitives.json").read_text(encoding="utf-8"))


def list_families() -> list[dict]:
    return list(load_taxonomy()["families"])


def list_primitives() -> list[dict]:
    return list(load_taxonomy()["primitives"])


def primitive_ids() -> list[str]:
    return [p["id"] for p in list_primitives()]


def get_primitive(pid: str) -> dict:
    for row in list_primitives():
        if row["id"] == pid:
            return row
    raise AskError(f"Unknown analytics primitive {pid!r}")


def list_composites() -> list[dict]:
    return list(load_taxonomy().get("composites") or [])


def get_composite(cid: str) -> dict:
    for row in list_composites():
        if row["id"] == cid:
            return row
    raise AskError(f"Unknown analytics composite {cid!r}")


def _fact(graph: SchemaGraph):
    return pick_fact(graph)


def _measure(graph: SchemaGraph, args: dict):
    return pick_measure(graph, args.get("measure"))


def _dim(graph: SchemaGraph, args: dict, key: str = "dimension"):
    return pick_dimension(graph, args.get(key))


def _date(graph: SchemaGraph, args: dict):
    return pick_date(graph, args.get("date"))


def _col(graph: SchemaGraph, name: str | None, fallback=None):
    if name:
        return resolve_column(graph, name)
    if fallback is not None:
        return fallback()
    raise AskError("column is required")


def _unwrap(op: dict | None) -> dict | None:
    if op and op.get("kind") == "query":
        return op.get("op")
    return op


def _projected_aliases(op: dict | None) -> set[str]:
    names: set[str] = set()
    current = _unwrap(op)
    while current:
        if current.get("op") == "project":
            for it in current.get("items") or []:
                alias = it.get("alias")
                if alias:
                    names.add(str(alias).casefold())
        current = current.get("input") or current.get("left")
    return names


def _grouped(op: dict | None) -> bool:
    current = _unwrap(op)
    while current:
        if current.get("op") == "aggregate":
            return True
        current = current.get("input") or current.get("left")
    return False


def _needed(*bound) -> list[str]:
    names = []
    for b in bound:
        if b is None:
            continue
        if isinstance(b, str):
            names.append(b)
        else:
            names.append(b.entity_name)
    return names


def _base(graph: SchemaGraph, *bound, join_type: str = "inner"):
    fact = _fact(graph)
    return join_entities(graph, fact.name, _needed(*bound), join_type=join_type)


def _ensure(graph: SchemaGraph, op: dict | None, *bound, join_type: str = "inner") -> dict:
    current = _unwrap(op)
    if current is None:
        return _base(graph, *bound, join_type=join_type)
    return attach_entities(graph, current, _needed(*bound), join_type=join_type)


def _cmp_pred(graph: SchemaGraph, spec: dict, args: dict) -> tuple[dict, dict]:
    cmp = spec.get("cmp") or args.get("cmp") or "="
    if spec["id"].startswith("measure_") or spec["id"] in {"threshold_above", "threshold_below"}:
        bound = _measure(graph, args)
        value = args.get("threshold", args.get("value", 0))
        if spec["id"] == "measure_positive":
            cmp, value = ">", 0
        elif spec["id"] == "measure_negative":
            cmp, value = "<", 0
        elif spec["id"] == "measure_zero":
            cmp, value = "=", 0
        elif spec["id"] == "measure_below" or spec["id"] == "threshold_below":
            cmp = "<"
        elif spec["id"] == "measure_above" or spec["id"] == "threshold_above":
            cmp = ">"
        pred = binop(cmp, col(bound.entity_name, bound.attr_name), lit(value))
        return bound, pred
    if spec["id"] == "year_like":
        bound = _date(graph, args) or _col(graph, args.get("column") or args.get("date"))
        pred = binop("like", col(bound.entity_name, bound.attr_name), lit(f"{args.get('year') or '2016'}-%"))
        return bound, pred
    if spec["id"] == "prefix_like":
        bound = _col(graph, args.get("column"))
        pred = binop("like", col(bound.entity_name, bound.attr_name), lit(f"{args.get('value') or ''}%"))
        return bound, pred
    if spec["id"] == "sample_eq":
        bound = _col(graph, args.get("column") or args.get("dimension"))
        value = (bound.attr.samples[0] if bound.attr.samples else args.get("value") or "x")
        pred = binop("=", col(bound.entity_name, bound.attr_name), lit(value))
        return bound, pred
    bound = _col(graph, args.get("column") or args.get("dimension") or args.get("measure"))
    if cmp in {"in", "not in", "between"}:
        values = args.get("values") or args.get("value") or []
        if not isinstance(values, (list, tuple)):
            values = [values]
        if cmp == "between" and len(values) < 2:
            values = [0, values[0] if values else 1]
        pred = binop(cmp, col(bound.entity_name, bound.attr_name), lit(list(values)))
        return bound, pred
    if cmp in {"is null", "is not null"}:
        pred = binop(cmp, col(bound.entity_name, bound.attr_name), lit(None))
        return bound, pred
    pred = binop(cmp, col(bound.entity_name, bound.attr_name), lit(args.get("value")))
    return bound, pred


def _slice_keys(graph: SchemaGraph, dim, value) -> dict:
    pk = dim.entity.pk_attrs()
    key = pk[0].name if pk else dim.attr_name
    return project(
        filt(scan(dim.entity_name), binop("=", col(dim.entity_name, dim.attr_name), lit(value))),
        col_item(dim.entity_name, key, "id"),
    )


def _all_keys(graph: SchemaGraph, dim) -> dict:
    pk = dim.entity.pk_attrs()
    key = pk[0].name if pk else dim.attr_name
    return project(scan(dim.entity_name), col_item(dim.entity_name, key, "id"))


def apply_primitive(graph: SchemaGraph, pid: str, op: dict | None = None, args: dict | None = None) -> dict:
    spec = get_primitive(pid)
    args = dict(args or {})
    op = _unwrap(op)
    family = spec["family"]

    if op and op.get("op") == "with" and family != "source" and pid != "hypothesize":
        inner = apply_primitive(graph, pid, op.get("input"), args)
        return {"op": "with", "ctes": op.get("ctes") or [], "input": inner}

    if family == "source":
        return _source(graph, spec, args, op)
    if family == "grain":
        return _grain(graph, spec, args, op)
    if family == "restrict":
        return _restrict(graph, spec, args, op)
    if family == "project":
        return _project(graph, spec, args, op)
    if family == "aggregate":
        return _aggregate(graph, spec, args, op)
    if family == "window":
        return _window(graph, spec, args, op)
    if family == "set":
        return _setop(graph, spec, args)
    if family == "derive":
        return _derive(graph, spec, args, op)
    if family == "cut":
        return _cut(graph, spec, args, op)
    if family == "time":
        return _time(graph, spec, args, op)
    return apply_extended(
        graph,
        spec,
        op,
        args,
        apply_primitive,
        _ensure,
        _measure,
        _dim,
        _date,
    )


def _source(graph: SchemaGraph, spec: dict, args: dict, op: dict | None) -> dict:
    pid = spec["id"]
    if pid == "scan_fact":
        return scan(_fact(graph).name)
    if pid == "scan_entity":
        name = args.get("entity") or _fact(graph).name
        return scan(graph.require_entity(name).name)
    if pid == "scan_dimension":
        return scan(_dim(graph, args).entity_name)
    if pid == "scan_measure":
        return scan(_measure(graph, args).entity_name)
    if pid == "scan_date":
        d = _date(graph, args)
        return scan((d or _fact(graph)).entity_name if d else _fact(graph).name)
    if pid == "overlay":
        from revolverelate.analytics.vector_apply import apply_chunk

        return apply_chunk(graph, {"id": "overlay"}, None, args)
    if pid == "ask_log":
        from revolverelate.analytics.intent_apply import apply_world

        return apply_world(graph, spec, None, args, _ensure, _measure, _dim)
    if pid == "star_join":
        return _base(graph, _measure(graph, args), _dim(graph, args), _date(graph, args))
    if pid == "distinct_scan":
        dim = _dim(graph, args)
        return distinct(project(scan(dim.entity_name), col_item(dim.entity_name, dim.attr_name, dim.attr_name)))
    if pid == "sample_limit":
        return lim(_ensure(graph, op), int(args.get("n") or 20))
    if pid == "with_cte":
        inner = _ensure(graph, op)
        name = args.get("name") or "q"
        return {"op": "with", "ctes": [{"name": name, "input": inner}], "input": scan(name)}
    if pid == "values_one":
        return {"op": "values", "columns": ["id"], "rows": [[args.get("value") or 1]], "alias": "v"}
    return _ensure(graph, op)


def _grain(graph: SchemaGraph, spec: dict, args: dict, op: dict | None) -> dict:
    pid = spec["id"]
    fact = _fact(graph)
    if pid == "coverage_left":
        dim = _dim(graph, args)
        return join_entities(graph, dim.entity_name, [fact.name], join_type="left")
    if pid in {"join_left", "anti_join"}:
        target = args.get("entity") or _dim(graph, args).entity_name
        src = _ensure(graph, op or scan(fact.name), target, join_type="left")
        if pid == "anti_join":
            ent = graph.require_entity(target)
            pk = ent.pk_attrs()
            key = pk[0].name if pk else ent.attributes[0].name
            return filt(src, binop("is null", col(ent.name, key), lit(None)))
        return src
    if pid == "join_right":
        target = args.get("entity") or _dim(graph, args).entity_name
        return _ensure(graph, op or scan(fact.name), target, join_type="right")
    if pid in {"join_inner", "semi_join", "join_path", "join_fact_dim", "join_all_needed", "attach_date"}:
        needed = []
        if args.get("entity"):
            needed.append(args["entity"])
        if args.get("column"):
            needed.append(resolve_column(graph, args["column"]).entity_name)
        if args.get("dimension") or pid in {"join_fact_dim", "join_all_needed"}:
            needed.append(_dim(graph, args).entity_name)
        if args.get("measure") or pid == "join_all_needed":
            needed.append(_measure(graph, args).entity_name)
        if args.get("date") or pid in {"attach_date", "join_all_needed"}:
            d = _date(graph, args)
            if d:
                needed.append(d.entity_name)
        if not needed:
            needed.append(_dim(graph, args).entity_name)
        return _ensure(graph, op or scan(fact.name), *needed, join_type="inner")
    return _ensure(graph, op)


def _restrict(graph: SchemaGraph, spec: dict, args: dict, op: dict | None) -> dict:
    pid = spec["id"]
    if pid == "negate":
        inner = _ensure(graph, op)
        if inner.get("op") == "filter":
            return filt(inner["input"], {"expr": "un", "op": "not", "input": inner["predicate"]})
        return inner
    if pid == "and_also":
        bound, pred = _cmp_pred(graph, {**spec, "id": "eq", "cmp": "="}, args)
        current = _ensure(graph, op, bound)
        if current.get("op") == "filter":
            return filt(current["input"], binop("and", current["predicate"], pred))
        return filt(current, pred)
    if pid == "or_else":
        bound, pred = _cmp_pred(graph, {**spec, "id": "eq", "cmp": "="}, args)
        current = _ensure(graph, op, bound)
        if current.get("op") == "filter":
            return filt(current["input"], binop("or", current["predicate"], pred))
        return filt(current, pred)
    bound, pred = _cmp_pred(graph, spec, args)
    return filt(_ensure(graph, op, bound), pred)


def _project(graph: SchemaGraph, spec: dict, args: dict, op: dict | None) -> dict:
    pid = spec["id"]
    if pid == "limit":
        return lim(_ensure(graph, op), int(args.get("n") or 10))
    if pid == "offset":
        return lim(_ensure(graph, op), int(args.get("n") or 10), offset=int(args.get("offset") or 0))
    if pid == "sort_value_desc":
        if _grouped(op):
            return sort(_ensure(graph, op), {"expr": attr_ref("value"), "direction": "DESC"})
        m = _measure(graph, args)
        src = project(_ensure(graph, op, m), col_item(m.entity_name, m.attr_name, "value"))
        return sort(src, {"expr": col(m.entity_name, m.attr_name), "direction": "DESC"})
    if pid == "sort_asc" or pid == "sort_desc":
        bound = _col(graph, args.get("column") or args.get("measure") or args.get("dimension"))
        direction = "ASC" if pid == "sort_asc" else "DESC"
        src = _ensure(graph, op, bound)
        return sort(src, {"expr": col(bound.entity_name, bound.attr_name), "direction": direction})
    if pid == "distinct_col":
        dim = _dim(graph, args)
        return distinct(project(scan(dim.entity_name), col_item(dim.entity_name, dim.attr_name, dim.attr_name)))
    if pid == "project_star":
        return project(_ensure(graph, op), item({"expr": "star"}))
    if pid == "project_measure" or pid == "rename":
        m = _measure(graph, args)
        alias = args.get("alias") or ("value" if pid == "rename" else m.attr_name)
        return project(_ensure(graph, op, m), col_item(m.entity_name, m.attr_name, alias))
    if pid == "project_dimension":
        d = _dim(graph, args)
        return project(_ensure(graph, op, d), col_item(d.entity_name, d.attr_name, d.attr_name))
    if pid == "project_pair":
        m, d = _measure(graph, args), _dim(graph, args)
        return project(_ensure(graph, op, m, d), col_item(d.entity_name, d.attr_name, d.attr_name), col_item(m.entity_name, m.attr_name, "value"))
    return _ensure(graph, op)


def _aggregate(graph: SchemaGraph, spec: dict, args: dict, op: dict | None) -> dict:
    pid = spec["id"]
    if pid in {"sum_if", "count_if"}:
        m = _measure(graph, args)
        src = _ensure(graph, op, m)
        flagged = case([{"when": binop(">", col(m.entity_name, m.attr_name), lit(0)), "then": col(m.entity_name, m.attr_name) if pid == "sum_if" else lit(1)}], lit(0))
        return agg(src, [], agg_fn("sum", flagged, "value"))
    if pid == "weighted_avg":
        m = _measure(graph, args)
        w = pick_measure(graph, args.get("measure2"))
        src = _ensure(graph, op, m, w)
        return project(
            agg(
                src,
                [],
                agg_fn("sum", binop("*", col(m.entity_name, m.attr_name), col(w.entity_name, w.attr_name)), "num"),
                agg_fn("sum", col(w.entity_name, w.attr_name), "den"),
            ),
            item(binop("/", attr_ref("num"), fn("nullif", attr_ref("den"), lit(0))), "value"),
        )
    if pid.startswith("having_"):
        cmp = {">": "having_gt", "<": "having_lt", ">=": "having_ge", "<=": "having_le"}
        op_cmp = next(k for k, v in cmp.items() if v == pid)
        src = op if op is not None and op.get("op") == "aggregate" else apply_primitive(graph, "agg_sum_by", op, args)
        return filt(src, binop(op_cmp, attr_ref("value"), lit(float(args.get("threshold") or 0))))
    if pid == "group_1":
        d = _dim(graph, args)
        src = _ensure(graph, op, d)
        return agg(src, [col(d.entity_name, d.attr_name)], count_star("value"))
    if pid == "group_2":
        a, b = _dim(graph, args), pick_dimension(graph, args.get("dimension2"), avoid=_dim(graph, args))
        src = _ensure(graph, op, a, b)
        return agg(src, [col(a.entity_name, a.attr_name), col(b.entity_name, b.attr_name)], count_star("value"))
    if pid == "agg_count_by":
        d = _dim(graph, args)
        return agg(_ensure(graph, op, d), [col(d.entity_name, d.attr_name)], count_star("value"))
    if pid == "agg_sum_by" or pid == "agg_avg_by":
        m, d = _measure(graph, args), _dim(graph, args)
        agg_name = "avg" if pid == "agg_avg_by" else "sum"
        return agg(_ensure(graph, op, m, d), [col(d.entity_name, d.attr_name)], agg_fn(agg_name, col(m.entity_name, m.attr_name), "value"))
    if pid == "agg_count_star":
        return agg(_ensure(graph, op), [], count_star("value"))
    if pid == "agg_count_distinct":
        bound = _col(graph, args.get("column") or args.get("dimension"))
        expr = item({"expr": "agg", "fn": "count", "distinct": True, "input": col(bound.entity_name, bound.attr_name)}, "value")
        return agg(_ensure(graph, op, bound), [], expr)
    m = _measure(graph, args) if spec.get("fn") != "count" or pid == "agg_count" else None
    if pid == "agg_count" and m is None:
        m = _measure(graph, args)
    src = _ensure(graph, op, m)
    agg_fn_name = spec.get("fn") or "sum"
    if pid == "agg_count_star":
        return agg(src, [], count_star("value"))
    return agg(src, [], agg_fn(agg_fn_name, col(m.entity_name, m.attr_name), "value"))


def _window(graph: SchemaGraph, spec: dict, args: dict, op: dict | None) -> dict:
    pid = spec["id"]
    if pid == "win_share_total":
        grouped = apply_primitive(graph, "agg_sum_by", op, args)
        d = _dim(graph, args)
        return project(
            grouped,
            item(attr_ref(d.attr_name), d.attr_name),
            item(attr_ref("value"), "value"),
            item(over("sum", inp=attr_ref("value")), "total"),
        )
    if pid == "win_share_partition":
        m, d = _measure(graph, args), _dim(graph, args)
        src = _ensure(graph, op, m, d)
        return project(
            src,
            col_item(d.entity_name, d.attr_name, d.attr_name),
            col_item(m.entity_name, m.attr_name, "value"),
            item(over("sum", inp=col(m.entity_name, m.attr_name), partition=[col(d.entity_name, d.attr_name)]), "total"),
        )
    if pid == "win_rank_in_dim":
        m, d = _measure(graph, args), _dim(graph, args)
        src = _ensure(graph, op, m, d)
        return project(
            src,
            col_item(d.entity_name, d.attr_name, d.attr_name),
            col_item(m.entity_name, m.attr_name, "value"),
            item(
                over("rank", partition=[col(d.entity_name, d.attr_name)], order=[{"expr": col(m.entity_name, m.attr_name), "direction": "DESC"}]),
                "rk",
            ),
        )
    if pid == "period_delta":
        m, d = _measure(graph, args), _date(graph, args)
        src = _ensure(graph, op, m, d)
        order = [{"expr": col(d.entity_name, d.attr_name), "direction": "ASC"}] if d else [{"expr": col(m.entity_name, m.attr_name), "direction": "ASC"}]
        return project(
            src,
            col_item(m.entity_name, m.attr_name, "value"),
            item(over("lag", inp=col(m.entity_name, m.attr_name), order=order), "prior"),
            item(binop("-", col(m.entity_name, m.attr_name), over("lag", inp=col(m.entity_name, m.attr_name), order=order)), "delta"),
        )
    if pid == "cume_dist":
        m = _measure(graph, args)
        src = _ensure(graph, op, m)
        return project(
            src,
            col_item(m.entity_name, m.attr_name, "value"),
            item(over("cume_dist", order=[{"expr": col(m.entity_name, m.attr_name), "direction": "ASC"}]), "cume"),
        )
    if pid == "win_running_by_date":
        m, d = _measure(graph, args), _date(graph, args)
        src = _ensure(graph, op, m, d)
        order = [{"expr": col(d.entity_name, d.attr_name), "direction": "ASC"}] if d else [{"expr": col(m.entity_name, m.attr_name), "direction": "ASC"}]
        return project(src, col_item(m.entity_name, m.attr_name, "value"), item(over("sum", inp=col(m.entity_name, m.attr_name), order=order), "running"))
    m = None
    try:
        m = _measure(graph, args)
    except Exception:
        m = None
    d = None
    fn = spec.get("fn") or "rank"
    if fn in {"lag", "lead", "first_value", "last_value", "sum", "avg", "count"}:
        d = _date(graph, args)
    src = _ensure(graph, op, m, d)
    order = [{"expr": col(m.entity_name, m.attr_name), "direction": "DESC"}] if m else []
    if fn == "ntile":
        expr = {"expr": "over", "fn": "ntile", "input": lit(int(args.get("n") or 4)), "order": order}
    elif fn in {"lag", "lead", "first_value", "last_value", "sum", "avg", "count"}:
        inner = col(m.entity_name, m.attr_name) if m else {"expr": "star"}
        ord2 = [{"expr": col(d.entity_name, d.attr_name), "direction": "ASC"}] if d else order
        if fn == "count" and m is None:
            expr = over("count", inp={"expr": "star"}, order=ord2)
        else:
            expr = over(fn, inp=inner, order=ord2)
    else:
        expr = over(fn, order=order)
    items = []
    if m:
        items.append(col_item(m.entity_name, m.attr_name, "value"))
    items.append(item(expr, fn if fn not in {"sum", "avg", "count"} else "running"))
    return project(src, *items)


def _setop(graph: SchemaGraph, spec: dict, args: dict) -> dict:
    dim = _dim(graph, args)
    left_v = args.get("left") or args.get("value") or (dim.attr.samples[0] if dim.attr.samples else "A")
    right_v = args.get("right") or (dim.attr.samples[1] if len(dim.attr.samples) > 1 else left_v)
    pid = spec["id"]
    if pid in {"set_union", "union_two_slices"}:
        return {"op": "setop", "set": "union", "all": False, "left": _slice_keys(graph, dim, left_v), "right": _slice_keys(graph, dim, right_v)}
    if pid == "set_union_all":
        return {"op": "setop", "set": "union", "all": True, "left": _slice_keys(graph, dim, left_v), "right": _slice_keys(graph, dim, right_v)}
    if pid in {"set_except", "except_slice"}:
        return {"op": "setop", "set": "except", "left": _all_keys(graph, dim), "right": _slice_keys(graph, dim, left_v)}
    if pid == "set_intersect":
        return {"op": "setop", "set": "intersect", "left": _all_keys(graph, dim), "right": _slice_keys(graph, dim, left_v)}
    return _slice_keys(graph, dim, left_v)


def _derive(graph: SchemaGraph, spec: dict, args: dict, op: dict | None) -> dict:
    pid = spec["id"]
    a = _measure(graph, args)
    if pid == "case_negative":
        return filt(_ensure(graph, op, a), binop("<", col(a.entity_name, a.attr_name), lit(0)))
    if pid == "abs_fn":
        src = _ensure(graph, op, a)
        return project(src, item({"expr": "fn", "fn": "abs", "args": [col(a.entity_name, a.attr_name)]}, "value"))
    if pid == "coalesce_zero":
        src = _ensure(graph, op, a)
        return project(src, item({"expr": "fn", "fn": "coalesce", "args": [col(a.entity_name, a.attr_name), lit(0)]}, "value"))
    if pid == "invert":
        src = _ensure(graph, op, a)
        return project(src, item(binop("/", lit(1), col(a.entity_name, a.attr_name)), "value"))
    if pid == "case_when":
        src = _ensure(graph, op, a)
        thr = float(args.get("threshold") or 0)
        return project(
            src,
            item(case([{"when": binop(">", col(a.entity_name, a.attr_name), lit(thr)), "then": col(a.entity_name, a.attr_name)}], lit(0)), "value"),
        )
    b = pick_measure(graph, args.get("measure2") or ("Profit" if a.attr_name.casefold() != "profit" else "Sales"))
    aliases = _projected_aliases(op)
    left = attr_ref(a.attr_name) if a.attr_name.casefold() in aliases else col(a.entity_name, a.attr_name)
    right = attr_ref(b.attr_name) if b.attr_name.casefold() in aliases else col(b.entity_name, b.attr_name)
    src = op if aliases else _ensure(graph, op, a, b)
    if pid == "safe_div":
        return project(src, item(binop("/", left, fn("nullif", right, lit(0))), "value"))
    if pid == "margin_pct":
        return project(src, item(binop("/", binop("-", left, right), fn("nullif", left, lit(0))), "value"))
    op_sym = {"ratio": "/", "divide": "/", "difference": "-", "subtract": "-", "product": "*", "add": "+"}.get(pid, "/")
    return project(src, item(binop(op_sym, left, right), "value"))


def _cut(graph: SchemaGraph, spec: dict, args: dict, op: dict | None) -> dict:
    pid = spec["id"]
    if pid == "top_pct":
        m = _measure(graph, args)
        src = _ensure(graph, op, m)
        ranked = project(
            src,
            col_item(m.entity_name, m.attr_name, "value"),
            item(over("percent_rank", order=[{"expr": col(m.entity_name, m.attr_name), "direction": "DESC"}]), "p"),
        )
        return filt(ranked, binop("<=", attr_ref("p"), lit(float(args.get("p") or 0.1))))
    if pid == "above_mean":
        if _grouped(op) or (op and op.get("op") in {"project", "with", "filter"}):
            scored = project(
                op,
                item(attr_ref("value"), "value"),
                item(over("avg", inp=attr_ref("value")), "mean"),
            )
            return filt(scored, binop(">", attr_ref("value"), attr_ref("mean")))
        m = _measure(graph, args)
        src = _ensure(graph, op, m)
        return filt(
            project(src, col_item(m.entity_name, m.attr_name, "value"), item(over("avg", inp=col(m.entity_name, m.attr_name)), "mean")),
            binop(">", attr_ref("value"), attr_ref("mean")),
        )
    if pid in {"threshold_above", "threshold_below"}:
        return _restrict(graph, spec, args, op)
    if pid == "pareto_running":
        grouped = apply_primitive(graph, "agg_sum_by", op, args)
        grouped = sort(grouped, {"expr": attr_ref("value"), "direction": "DESC"})
        d = _dim(graph, args)
        return project(
            grouped,
            item(attr_ref(d.attr_name), d.attr_name),
            item(attr_ref("value"), "value"),
            item(over("sum", inp=attr_ref("value"), order=[{"expr": attr_ref("value"), "direction": "DESC"}]), "running"),
        )
    if pid == "top_n_per_group":
        ranked = apply_primitive(graph, "win_rank_in_dim", op, args)
        return filt(ranked, binop("<=", attr_ref("rk"), lit(int(args.get("n") or 3))))
    if pid == "ntile_bucket":
        return apply_primitive(graph, "win_ntile", op, args)
    if pid == "outlier_top":
        args = {**args, "n": 3}
        pid = "top_n"
    n = int(args.get("n") or 5)
    direction = "ASC" if pid == "bottom_n" else "DESC"
    if _grouped(op):
        return lim(sort(_ensure(graph, op), {"expr": attr_ref("value"), "direction": direction}), n)
    m = _measure(graph, args)
    src = project(_ensure(graph, op, m), col_item(m.entity_name, m.attr_name, "value"))
    return lim(sort(src, {"expr": col(m.entity_name, m.attr_name), "direction": direction}), n)


def _time(graph: SchemaGraph, spec: dict, args: dict, op: dict | None) -> dict:
    pid = spec["id"]
    d = _date(graph, args)
    if pid == "period_year":
        return _restrict(graph, {**spec, "id": "year_like", "cmp": "like", "family": "restrict"}, args, op)
    if pid == "period_like":
        if not d:
            return _ensure(graph, op)
        return filt(_ensure(graph, op, d), binop("like", col(d.entity_name, d.attr_name), lit(args.get("value") or "%")))
    if pid == "order_by_date":
        if not d:
            return _ensure(graph, op)
        return sort(_ensure(graph, op, d), {"expr": col(d.entity_name, d.attr_name), "direction": "ASC"})
    if pid == "first_date":
        if not d:
            return apply_primitive(graph, "agg_min", op, {**args, "measure": pick_measure(graph).attr_name})
        return agg(_ensure(graph, op, d), [], agg_fn("min", col(d.entity_name, d.attr_name), "value"))
    if pid == "last_date":
        if not d:
            return apply_primitive(graph, "agg_max", op, args)
        return agg(_ensure(graph, op, d), [], agg_fn("max", col(d.entity_name, d.attr_name), "value"))
    if pid == "trailing_n" or pid == "trailing_period":
        src = _ensure(graph, op, d)
        if d:
            src = sort(src, {"expr": col(d.entity_name, d.attr_name), "direction": "DESC"})
        return lim(src, int(args.get("n") or 5))
    if pid in {"date_trunc_month", "extract_year", "yoy", "ytd"} and d:
        src = _ensure(graph, op, d)
        c = col(d.entity_name, d.attr_name)
        if pid == "date_trunc_month":
            return project(src, item(fn("substr", c, lit(1), lit(7)), "value"))
        if pid == "extract_year":
            return project(src, item(fn("substr", c, lit(1), lit(4)), "value"))
        year = str(args.get("year") or "2016")
        if pid == "ytd":
            return filt(src, binop("like", c, lit(f"{year}-%")))
        prior = str(int(year) - 1)
        return filt(src, binop("or", binop("like", c, lit(f"{year}-%")), binop("like", c, lit(f"{prior}-%"))))
    return _ensure(graph, op)


def chain(graph: SchemaGraph, steps: list[dict]) -> dict:
    """Compose primitives. Each step is {op, ...binds}. Result is a query RelOp."""
    from revolverelate.analytics.composites import assert_chain

    assert_chain(steps)
    current = None
    for step in steps:
        pid = step.get("op") or step.get("primitive") or step.get("id")
        if not pid:
            raise AskError("chain step missing op")
        binds = {k: v for k, v in step.items() if k not in {"op", "primitive", "id"}}
        current = apply_primitive(graph, str(pid), current, binds)
    if current is None:
        current = scan(_fact(graph).name)
    return query(current)


def default_binds(graph: SchemaGraph) -> dict:
    """Schema-agnostic defaults so every primitive can apply on any graph."""
    m = pick_measure(graph)
    d = pick_dimension(graph)
    d2 = None
    try:
        d2 = pick_dimension(graph, None, avoid=d)
        if d2.attr_name.casefold() == d.attr_name.casefold():
            d2 = None
    except Exception:
        d2 = None
    date = pick_date(graph)
    sample = d.attr.samples[0] if d.attr.samples else "x"
    sample2 = d.attr.samples[1] if len(d.attr.samples) > 1 else sample
    m2 = None
    try:
        other = [x for x in list_measures(graph) if x.casefold() != m.attr_name.casefold()]
        m2 = other[0] if other else m.attr_name
    except Exception:
        m2 = m.attr_name
    return {
        "measure": m.attr_name,
        "measure2": m2,
        "dimension": d.attr_name,
        "dimension2": (d2.attr_name if d2 else d.attr_name),
        "column": d.attr_name,
        "date": date.attr_name if date else d.attr_name,
        "entity": _fact(graph).name,
        "value": sample,
        "values": [sample, sample2],
        "left": sample,
        "right": sample2,
        "year": "2016",
        "n": 5,
        "threshold": 0,
        "offset": 0,
        "alias": "value",
        "name": "q",
        "p": 0.9,
        "query": "bookcase",
        "strategy": "semantic",
        "model": "hash-16",
        "k": 5,
        "objective": "west sales by product retrieve",
    }


apply = apply_primitive
