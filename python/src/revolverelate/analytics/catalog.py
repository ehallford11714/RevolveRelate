"""Named analytics recipes. Each one scaffolds RelOp from schema primitives."""

from __future__ import annotations

from collections.abc import Callable

from revolverelate.analytics.bind import (
    BoundCol,
    pick_date,
    pick_dimension,
    pick_fact,
    pick_measure,
    resolve_column,
)
from revolverelate.ir.rel import (
    agg,
    agg_fn,
    attr_ref,
    binop,
    col,
    col_item,
    count_star,
    distinct,
    filt,
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


def _needed(*cols: BoundCol | None) -> list[str]:
    names = []
    for col_ in cols:
        if col_ is not None:
            names.append(col_.entity_name)
    return names


def _base(graph: SchemaGraph, *cols: BoundCol | None, join_type: str = "inner"):
    fact = pick_fact(graph)
    return join_entities(graph, fact.name, _needed(*cols), join_type=join_type)


def _eq_sample(dim: BoundCol, value: str | None) -> dict | None:
    if value:
        return binop("=", col(dim.entity_name, dim.attr_name), lit(value))
    if dim.attr.samples:
        return binop("=", col(dim.entity_name, dim.attr_name), lit(dim.attr.samples[0]))
    return None


def recipe_sum_by_dimension(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    dim = pick_dimension(graph, args.get("dimension"), avoid=measure)
    src = _base(graph, measure, dim)
    return query(
        sort(
            agg(
                src,
                [col(dim.entity_name, dim.attr_name)],
                agg_fn("sum", col(measure.entity_name, measure.attr_name), "value"),
            ),
            {"expr": attr_ref("value"), "direction": "DESC"},
        )
    )


def recipe_count_by_dimension(graph: SchemaGraph, args: dict) -> dict:
    dim = pick_dimension(graph, args.get("dimension"))
    src = _base(graph, dim)
    return query(agg(src, [col(dim.entity_name, dim.attr_name)], count_star("n")))


def recipe_avg_measure(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    src = _base(graph, measure)
    return query(agg(src, [], agg_fn("avg", col(measure.entity_name, measure.attr_name), "value")))


def recipe_top_n(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    n = int(args.get("n") or 5)
    src = _base(graph, measure)
    return query(
        lim(
            sort(
                project(src, col_item(measure.entity_name, measure.attr_name, "value")),
                {"expr": col(measure.entity_name, measure.attr_name), "direction": "DESC"},
            ),
            n,
        )
    )


def recipe_share_of_total(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    dim = pick_dimension(graph, args.get("dimension"), avoid=measure)
    grouped = agg(
        _base(graph, measure, dim),
        [col(dim.entity_name, dim.attr_name)],
        agg_fn("sum", col(measure.entity_name, measure.attr_name), "value"),
    )
    return query(
        project(
            grouped,
            item(attr_ref(dim.attr_name), dim.attr_name),
            item(attr_ref("value"), "value"),
            item(over("sum", inp=attr_ref("value")), "total"),
        )
    )


def recipe_rank_within(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    dim = pick_dimension(graph, args.get("dimension"), avoid=measure)
    src = _base(graph, measure, dim)
    return query(
        project(
            src,
            col_item(dim.entity_name, dim.attr_name, dim.attr_name),
            col_item(measure.entity_name, measure.attr_name, "value"),
            item(
                over(
                    "rank",
                    partition=[col(dim.entity_name, dim.attr_name)],
                    order=[{"expr": col(measure.entity_name, measure.attr_name), "direction": "DESC"}],
                ),
                "rk",
            ),
        )
    )


def recipe_having_above(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    dim = pick_dimension(graph, args.get("dimension"), avoid=measure)
    threshold = float(args.get("threshold") or 0)
    grouped = agg(
        _base(graph, measure, dim),
        [col(dim.entity_name, dim.attr_name)],
        agg_fn("sum", col(measure.entity_name, measure.attr_name), "value"),
    )
    return query(filt(grouped, binop(">", attr_ref("value"), lit(threshold))))


def recipe_multi_group(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    dim_a = pick_dimension(graph, args.get("dimension"))
    other = args.get("dimension2")
    dims = [a.name for e in graph.all_entities() for a in e.attributes]
    dim_b = pick_dimension(graph, other) if other else None
    if dim_b is None:
        for name in dims:
            if name.casefold() != dim_a.attr_name.casefold():
                try:
                    cand = pick_dimension(graph, name, avoid=dim_a)
                except Exception:
                    continue
                if cand.attr_name.casefold() != dim_a.attr_name.casefold():
                    dim_b = cand
                    break
    if dim_b is None:
        dim_b = dim_a
    src = _base(graph, measure, dim_a, dim_b)
    return query(
        sort(
            agg(
                src,
                [col(dim_a.entity_name, dim_a.attr_name), col(dim_b.entity_name, dim_b.attr_name)],
                agg_fn("sum", col(measure.entity_name, measure.attr_name), "value"),
            ),
            {"expr": attr_ref("value"), "direction": "DESC"},
        )
    )


def recipe_mix_filter_agg(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    dim = pick_dimension(graph, args.get("dimension"), avoid=measure)
    pred = _eq_sample(dim, args.get("value"))
    src = _base(graph, measure, dim)
    if pred:
        src = filt(src, pred)
    if args.get("min"):
        src = filt(src, binop(">", col(measure.entity_name, measure.attr_name), lit(float(args["min"]))))
    return query(
        agg(src, [col(dim.entity_name, dim.attr_name)], agg_fn("sum", col(measure.entity_name, measure.attr_name), "value"))
    )


def recipe_running_sum(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    date = pick_date(graph, args.get("date"))
    src = _base(graph, measure, date)
    order = [{"expr": col(date.entity_name, date.attr_name), "direction": "ASC"}] if date else [
        {"expr": col(measure.entity_name, measure.attr_name), "direction": "ASC"}
    ]
    return query(
        project(
            src,
            col_item(measure.entity_name, measure.attr_name, "value"),
            item(over("sum", inp=col(measure.entity_name, measure.attr_name), order=order), "running"),
        )
    )


def recipe_period_slice(graph: SchemaGraph, args: dict) -> dict:
    measure = pick_measure(graph, args.get("measure"))
    date = pick_date(graph, args.get("date"))
    dim = pick_dimension(graph, args.get("dimension"), avoid=measure)
    year = str(args.get("year") or "2016")
    src = join_entities(graph, pick_fact(graph).name, _needed(measure, date, dim))
    if date:
        src = filt(src, binop("like", col(date.entity_name, date.attr_name), lit(f"{year}-%")))
    return query(
        agg(src, [col(dim.entity_name, dim.attr_name)], agg_fn("sum", col(measure.entity_name, measure.attr_name), "value"))
    )


def recipe_union_segments(graph: SchemaGraph, args: dict) -> dict:
    dim = pick_dimension(graph, args.get("dimension") or "Region")
    left_v = args.get("left") or (dim.attr.samples[0] if dim.attr.samples else "West")
    right_v = args.get("right") or (dim.attr.samples[1] if len(dim.attr.samples) > 1 else left_v)
    pk = dim.entity.pk_attrs()
    key = pk[0].name if pk else dim.attr_name

    def branch(value) -> dict:
        return project(
            filt(scan(dim.entity_name), binop("=", col(dim.entity_name, dim.attr_name), lit(value))),
            col_item(dim.entity_name, key, "id"),
        )

    return query({"op": "setop", "set": "union", "all": False, "left": branch(left_v), "right": branch(right_v)})


def recipe_distinct_dimension(graph: SchemaGraph, args: dict) -> dict:
    dim = pick_dimension(graph, args.get("dimension"))
    return query(distinct(project(scan(dim.entity_name), col_item(dim.entity_name, dim.attr_name, dim.attr_name))))


def recipe_coverage_left(graph: SchemaGraph, args: dict) -> dict:
    """Left-join fact onto a dimension entity and count matches."""
    dim = pick_dimension(graph, args.get("dimension"))
    fact = pick_fact(graph)
    src = join_entities(graph, dim.entity_name, [fact.name], join_type="left")
    return query(agg(src, [col(dim.entity_name, dim.attr_name)], count_star("n")))


def recipe_pareto(graph: SchemaGraph, args: dict) -> dict:
    """Top contributors plus a running share window."""
    measure = pick_measure(graph, args.get("measure"))
    dim = pick_dimension(graph, args.get("dimension"), avoid=measure)
    grouped = sort(
        agg(
            _base(graph, measure, dim),
            [col(dim.entity_name, dim.attr_name)],
            agg_fn("sum", col(measure.entity_name, measure.attr_name), "value"),
        ),
        {"expr": attr_ref("value"), "direction": "DESC"},
    )
    return query(
        project(
            grouped,
            item(attr_ref(dim.attr_name), dim.attr_name),
            item(attr_ref("value"), "value"),
            item(
                over("sum", inp=attr_ref("value"), order=[{"expr": attr_ref("value"), "direction": "DESC"}]),
                "running",
            ),
        )
    )


def recipe_from_column(graph: SchemaGraph, args: dict) -> dict:
    """Escape hatch: named column equality filter on the fact star."""
    measure = pick_measure(graph, args.get("measure"))
    column = resolve_column(graph, args.get("column") or measure.attr_name)
    src = _base(graph, measure, column)
    if args.get("value") is not None:
        src = filt(src, binop("=", col(column.entity_name, column.attr_name), lit(args["value"])))
    return query(lim(project(src, col_item(measure.entity_name, measure.attr_name, "value")), int(args.get("n") or 50)))


RecipeFn = Callable[[SchemaGraph, dict], dict]

RECIPES: dict[str, dict] = {
    "sum_by_dimension": {
        "title": "Sum a measure grouped by a dimension",
        "binds": ["measure", "dimension"],
        "build": recipe_sum_by_dimension,
    },
    "count_by_dimension": {
        "title": "Count rows grouped by a dimension",
        "binds": ["dimension"],
        "build": recipe_count_by_dimension,
    },
    "avg_measure": {
        "title": "Average a measure",
        "binds": ["measure"],
        "build": recipe_avg_measure,
    },
    "top_n": {
        "title": "Top N fact rows by a measure",
        "binds": ["measure", "n"],
        "build": recipe_top_n,
    },
    "share_of_total": {
        "title": "Group sum plus window total (share scaffold)",
        "binds": ["measure", "dimension"],
        "build": recipe_share_of_total,
    },
    "rank_within": {
        "title": "Rank measure rows inside a dimension partition",
        "binds": ["measure", "dimension"],
        "build": recipe_rank_within,
    },
    "having_above": {
        "title": "Grouped sum with HAVING-style threshold",
        "binds": ["measure", "dimension", "threshold"],
        "build": recipe_having_above,
    },
    "multi_group": {
        "title": "Sum a measure by two dimensions",
        "binds": ["measure", "dimension", "dimension2"],
        "build": recipe_multi_group,
    },
    "mix_filter_agg": {
        "title": "Filter a slice, then aggregate",
        "binds": ["measure", "dimension", "value", "min"],
        "build": recipe_mix_filter_agg,
    },
    "running_sum": {
        "title": "Window running sum ordered by date",
        "binds": ["measure", "date"],
        "build": recipe_running_sum,
    },
    "period_slice": {
        "title": "Year-like period filter then group",
        "binds": ["measure", "dimension", "year", "date"],
        "build": recipe_period_slice,
    },
    "union_segments": {
        "title": "Union two dimension slices",
        "binds": ["dimension", "left", "right"],
        "build": recipe_union_segments,
    },
    "distinct_dimension": {
        "title": "Distinct values of a dimension",
        "binds": ["dimension"],
        "build": recipe_distinct_dimension,
    },
    "coverage_left": {
        "title": "Left-join coverage count (entity vs fact)",
        "binds": ["dimension"],
        "build": recipe_coverage_left,
    },
    "pareto": {
        "title": "Sorted contributors with running window sum",
        "binds": ["measure", "dimension"],
        "build": recipe_pareto,
    },
    "column_filter": {
        "title": "Filter a bound column then project the measure",
        "binds": ["measure", "column", "value", "n"],
        "build": recipe_from_column,
    },
}


def list_recipes() -> list[dict]:
    return [{"id": key, "title": row["title"], "binds": list(row["binds"])} for key, row in RECIPES.items()]


def get_recipe(recipe_id: str) -> dict:
    if recipe_id not in RECIPES:
        known = ", ".join(sorted(RECIPES))
        raise KeyError(f"Unknown analytics recipe {recipe_id!r}. Known: {known}")
    return RECIPES[recipe_id]


def scaffold_ir(recipe_id: str, graph: SchemaGraph, args: dict | None = None) -> dict:
    recipe = get_recipe(recipe_id)
    return recipe["build"](graph, dict(args or {}))
