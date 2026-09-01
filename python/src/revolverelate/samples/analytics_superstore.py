"""Superstore analytics examples: scaffold RelOp → dummy rollout → live promote."""

from __future__ import annotations

import json
from pathlib import Path

from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore

# Named use cases a Superstore analyst would actually run.
CASES = [
    {
        "id": "sales_by_region",
        "use_case": "Where does revenue concentrate?",
        "recipe": "sum_by_dimension",
        "args": {"measure": "Sales", "dimension": "Region"},
    },
    {
        "id": "sales_by_category",
        "use_case": "Which product category carries the book?",
        "recipe": "sum_by_dimension",
        "args": {"measure": "Sales", "dimension": "Category"},
    },
    {
        "id": "profit_by_segment",
        "use_case": "Which customer segment is actually profitable?",
        "recipe": "sum_by_dimension",
        "args": {"measure": "Profit", "dimension": "Segment"},
    },
    {
        "id": "orders_by_ship",
        "use_case": "How do we ship — mix of modes?",
        "recipe": "count_by_dimension",
        "args": {"dimension": "ShipMode"},
    },
    {
        "id": "customers_by_region",
        "use_case": "Headcount of the book of business by region.",
        "recipe": "count_by_dimension",
        "args": {"dimension": "Region"},
    },
    {
        "id": "avg_discount",
        "use_case": "What is typical discount pressure?",
        "recipe": "avg_measure",
        "args": {"measure": "Discount"},
    },
    {
        "id": "top_lines",
        "use_case": "Largest individual order lines (outliers / whales).",
        "recipe": "top_n",
        "args": {"measure": "Sales", "n": 5},
    },
    {
        "id": "category_share",
        "use_case": "Category mix: each category's sum next to a window total.",
        "recipe": "share_of_total",
        "args": {"measure": "Sales", "dimension": "Category"},
    },
    {
        "id": "rank_in_region",
        "use_case": "Rank lines inside each region by sales.",
        "recipe": "rank_within",
        "args": {"measure": "Sales", "dimension": "Region"},
    },
    {
        "id": "regions_over_floor",
        "use_case": "Regions whose dummy/live sales clear a floor (HAVING).",
        "recipe": "having_above",
        "args": {"measure": "Sales", "dimension": "Region", "threshold": 100},
    },
    {
        "id": "region_x_category",
        "use_case": "Two-way: region × category sales cube slice.",
        "recipe": "multi_group",
        "args": {"measure": "Sales", "dimension": "Region", "dimension2": "Category"},
    },
    {
        "id": "west_furniture",
        "use_case": "A filtered slice, then aggregated (West furniture-style mix).",
        "recipe": "mix_filter_agg",
        "args": {"measure": "Sales", "dimension": "Region", "value": "West", "min": 20},
    },
    {
        "id": "running_sales",
        "use_case": "Running sales total ordered by order date.",
        "recipe": "running_sum",
        "args": {"measure": "Sales", "date": "OrderDate"},
    },
    {
        "id": "year_2016",
        "use_case": "2016 book only, then sales by region.",
        "recipe": "period_slice",
        "args": {"measure": "Sales", "dimension": "Region", "year": "2016", "date": "OrderDate"},
    },
    {
        "id": "west_or_south",
        "use_case": "Union two territory slices (West ∪ South customers).",
        "recipe": "union_segments",
        "args": {"dimension": "Region", "left": "West", "right": "South"},
    },
    {
        "id": "distinct_states",
        "use_case": "Coverage: which states are in the book?",
        "recipe": "distinct_dimension",
        "args": {"dimension": "State"},
    },
    {
        "id": "region_coverage",
        "use_case": "Left-join coverage: customers per region vs their orders.",
        "recipe": "coverage_left",
        "args": {"dimension": "Region"},
    },
    {
        "id": "pareto_segment",
        "use_case": "Pareto of segments: sorted sales plus running window.",
        "recipe": "pareto",
        "args": {"measure": "Sales", "dimension": "Segment"},
    },
]


def run_superstore_analytics(workdir: str | Path, *, live_path: str | Path | None = None) -> dict:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    live = Path(live_path) if live_path else workdir / "superstore.sqlite"
    write_superstore(live)
    rr = RevolveRelate.connect(str(live), workdir=workdir)
    build = rr.build(refresh=True, rows_per_entity=8)
    steps = []
    for case in CASES:
        plan = rr.analytics.scaffold(case["recipe"], **case["args"])
        rolled = rr.analytics.rollout(plan["id"])
        live_out = rr.analytics.promote(plan["id"])
        steps.append(
            {
                "id": case["id"],
                "use_case": case["use_case"],
                "recipe": case["recipe"],
                "args": case["args"],
                "plan": plan["id"],
                "ir_op": plan["ir"]["op"]["op"],
                "sql": rolled["sql"],
                "params": rolled.get("params"),
                "sandbox_rows": rolled.get("rowCount"),
                "sandbox_sample": (rolled.get("rows") or [])[:4],
                "live_rows": live_out.get("liveRowCount"),
                "live_sample": (live_out.get("live") or {}).get("rows", [])[:4],
                "live_columns": (live_out.get("live") or {}).get("columns"),
                "status": live_out["status"],
            }
        )
    report = {
        "live_db": str(live),
        "build": build,
        "entities": [e.name for e in rr.schema.all_entities()],
        "cases": len(steps),
        "steps": steps,
    }
    rr.close()
    dest = workdir / "superstore-analytics.json"
    dest.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def print_report(report: dict) -> None:
    print("== Superstore analytics ==")
    print(f"live db: {report['live_db']}")
    print(f"build:   {report['build'].get('status')}  cases={report['cases']}")
    print()
    for step in report["steps"]:
        print(f"-- {step['id']}  [{step['recipe']}]")
        print(f"   {step['use_case']}")
        print(f"   RelOp: {step['ir_op']}")
        print(f"   SQL:   {step['sql']}")
        print(f"   sandbox rows: {step['sandbox_rows']}   live rows: {step['live_rows']}")
        print()
