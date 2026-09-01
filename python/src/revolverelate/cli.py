"""CLI: connect, build, ask, sandbox, promote, engines. Works via python -m revolverelate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from revolverelate.catalog import list_engines
from revolverelate.revolverelate import RevolveRelate
from revolverelate.slm.probe import slm_status


def _connect(args, workdir: Path) -> RevolveRelate:
    dsn = getattr(args, "dsn", None) or str(workdir / ".revolverelate" / "sandbox.sqlite")
    rr = RevolveRelate.connect(dsn, workdir=workdir)
    if not rr.cache.is_complete():
        if not getattr(args, "dsn", None):
            raise SystemExit("build() has not completed; pass --dsn and run build first")
        rr.build()
    return rr


def _recipe_args(args) -> dict:
    return {
        "measure": getattr(args, "measure", None),
        "dimension": getattr(args, "dimension", None),
        "dimension2": getattr(args, "dimension2", None),
        "value": getattr(args, "value", None),
        "year": getattr(args, "year", None),
        "n": getattr(args, "n", None),
        "threshold": getattr(args, "threshold", None),
        "min": getattr(args, "min_value", None),
        "left": getattr(args, "left", None),
        "right": getattr(args, "right", None),
    }


def _analytics_cmd(args, workdir: Path) -> int:
    rr = _connect(args, workdir)
    try:
        if args.an_cmd == "list":
            print(json.dumps(rr.analytics.list(), indent=2))
            return 0
        if args.an_cmd == "scaffold":
            plan = rr.analytics.scaffold(args.recipe, **_recipe_args(args))
            print(json.dumps({k: plan[k] for k in plan if k != "ir"}, indent=2, default=str))
            print(json.dumps(plan["ir"], indent=2))
            return 0
        if args.an_cmd == "rollout":
            plan = rr.analytics.rollout(args.plan)
            out = {k: plan[k] for k in plan if k not in {"rows", "ir"}}
            print(json.dumps(out, indent=2, default=str))
            return 0
        if args.an_cmd == "promote":
            plan = rr.analytics.promote(args.plan, allow_live=args.allow_live)
            print(json.dumps({k: plan[k] for k in plan if k not in {"rows", "ir", "live"}}, indent=2, default=str))
            print(json.dumps(plan.get("live"), indent=2, default=str))
            return 0
        if args.an_cmd == "run":
            plan = rr.analytics.run(args.recipe, **_recipe_args(args))
            print(json.dumps({k: plan[k] for k in plan if k not in {"rows", "ir"}}, indent=2, default=str))
            return 0
        if args.an_cmd == "primitives":
            listed = rr.analytics.list()
            print(json.dumps({k: listed[k] for k in ("families", "primitives", "composites") if k in listed}, indent=2))
            return 0
        if args.an_cmd == "chain":
            steps = None
            if getattr(args, "steps", None):
                steps = json.loads(args.steps)
            if getattr(args, "steps_file", None):
                steps = json.loads(Path(args.steps_file).read_text(encoding="utf-8"))
            if getattr(args, "rollout", False):
                plan = rr.analytics.run_chain(steps, composite=getattr(args, "composite", None))
            else:
                plan = rr.analytics.scaffold_chain(steps, composite=getattr(args, "composite", None))
            print(json.dumps({k: plan[k] for k in plan if k not in {"rows", "ir"}}, indent=2, default=str))
            if plan.get("ir"):
                print(json.dumps(plan["ir"], indent=2))
            return 0
    finally:
        rr.close()
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="revolverelate",
        description="NL → relational algebra → dummy sandbox → live push after a complete build cache.",
    )
    parser.add_argument("--workdir", default=".", help="Directory for .revolverelate/ build cache")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_c = sub.add_parser("connect", help="Parse a DSN and print the engine family")
    p_c.add_argument("dsn")

    p_b = sub.add_parser("build", help="Introspect, impute primitives, write graph + dummy sandbox (once)")
    p_b.add_argument("dsn")
    p_b.add_argument("--refresh", action="store_true")
    p_b.add_argument("--rows", type=int, default=8)

    p_a = sub.add_parser("ask", help="NL → RelOp → SQL on the dummy sandbox")
    p_a.add_argument("question")
    p_a.add_argument("--dsn", default=None)

    p_p = sub.add_parser("promote", help="Replay a sandbox-validated IR against live (build must be complete)")
    p_p.add_argument("--dsn", required=True)
    p_p.add_argument("--ir", required=True, help="Path to RelOp JSON")
    p_p.add_argument("--allow-live", action="store_true")

    p_s = sub.add_parser("sql", help="Compile RelOp JSON to dialect SQL (no execute)")
    p_s.add_argument("ir")
    p_s.add_argument("--engine", default="sqlite")

    sub.add_parser("engines", help="List catalogued engines")
    sub.add_parser("slm", help="Probe local/cloud SLM")
    p_serve = sub.add_parser("serve", help="Agent HTTP surface")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8788)
    p_serve.add_argument("--dsn", default=":memory:")

    p_demo = sub.add_parser("demo", help="Superstore demo HTTP for the Node / Vite / Streamlit clients")
    p_demo.add_argument("--host", default="127.0.0.1")
    p_demo.add_argument("--port", type=int, default=8788)
    p_demo.add_argument("--root", default=None, help="Directory for superstore.sqlite (default demo/data)")

    p_mcp = sub.add_parser("mcp", help="Dedicated MCP server (stdio) for any agent host")
    p_mcp.add_argument("--jsonl", action="store_true")
    p_mcp.add_argument("--install", action="store_true", help="Print host MCP install JSON and exit")

    p_ss = sub.add_parser("superstore", help="Write a local Tableau-style Superstore SQLite database")
    p_ss.add_argument("--dest", default=None, help="Output .sqlite path")

    p_gene = sub.add_parser("gene", help="Write a public NCBI FASTA / pineoblastoma gene SQLite sample")
    p_gene.add_argument("--dest", default=None, help="Output .sqlite path")

    p_am = sub.add_parser("automine", help="Mine a corpus, RelOp-reflect, expand catalogued follow-ons, mine again")
    p_am.add_argument("--dsn", default=None, help="Live corpus sqlite / DSN (default: write gene.sqlite)")
    p_am.add_argument("--question", default="what causes pinealblastoma")
    p_am.add_argument("--passes", type=int, default=3)
    p_am.add_argument("--dest", default=None, help="When --dsn is omitted, write the gene sample here")
    p_am.add_argument("--no-report", action="store_true", help="Skip the citation-grounded report after automine")

    p_rep = sub.add_parser("report", help="Draft a citation-grounded report from automine findings (local SLM or cloud API)")
    p_rep.add_argument("--dsn", default=None, help="Live corpus sqlite / DSN when automine has not been saved yet")
    p_rep.add_argument("--question", default=None, help="If no automine.json exists, run automine first")
    p_rep.add_argument("--passes", type=int, default=3)
    p_rep.add_argument("--dest", default=None, help="When --dsn is omitted, write the gene sample here")
    p_rep.add_argument("--markdown", action="store_true", help="Print report.md instead of JSON")

    p_ex = sub.add_parser("example", help="Run the Superstore live walkthrough (connect, build, ask, promote)")
    p_ex.add_argument("--dest", default=None, help="Live Superstore sqlite path")

    p_exa = sub.add_parser("example-analytics", help="Run Superstore analytics recipes: scaffold → sandbox → live")
    p_exa.add_argument("--dest", default=None, help="Live Superstore sqlite path")

    p_tut = sub.add_parser("tutorial", help="Walk Superstore facts, overlay RAG chunks, then gene automine + report")
    p_tut.add_argument("--root", default="tutorial-run", help="Directory for sqlite files and .revolverelate caches")
    p_tut.add_argument("--skip", action="append", default=[], help="Skip a part: superstore, rag, or automine (repeatable)")
    p_tut.add_argument("--passes", type=int, default=3, help="Automine passes (default 3)")
    p_tut.add_argument("--json", action="store_true", help="Print tutorial.json instead of the narrated walkthrough")

    p_rag = sub.add_parser("rag", help="Semantic or causal retrieve on OverlayChunk (dummy RelOp, then live text)")
    p_rag.add_argument("query")
    p_rag.add_argument("--dsn", required=True)
    p_rag.add_argument("--strategy", default="semantic", help="semantic or causal (also topic, event, … via RelOp)")
    p_rag.add_argument("--column", default="ProductName")
    p_rag.add_argument("--n", type=int, default=5)

    p_an = sub.add_parser("analytics", help="Scaffold RelOp analytics, roll out on the dummy sandbox, then promote")
    an = p_an.add_subparsers(dest="an_cmd", required=True)
    an.add_parser("list", help="Recipes plus measures/dimensions from the built schema")
    p_sc = an.add_parser("scaffold", help="Bind a recipe to the schema and write a RelOp plan (no execute)")
    p_sc.add_argument("recipe")
    p_sc.add_argument("--dsn", default=None)
    p_sc.add_argument("--measure")
    p_sc.add_argument("--dimension")
    p_sc.add_argument("--dimension2")
    p_sc.add_argument("--value")
    p_sc.add_argument("--year")
    p_sc.add_argument("--n", type=int)
    p_sc.add_argument("--threshold", type=float)
    p_sc.add_argument("--min", dest="min_value", type=float)
    p_sc.add_argument("--left")
    p_sc.add_argument("--right")
    p_ro = an.add_parser("rollout", help="Run a scaffolded plan on the local duplicate only")
    p_ro.add_argument("plan")
    p_ro.add_argument("--dsn", default=None)
    p_pr = an.add_parser("promote", help="Replay a rolled-out plan against live")
    p_pr.add_argument("plan")
    p_pr.add_argument("--dsn", required=True)
    p_pr.add_argument("--allow-live", action="store_true")
    p_run = an.add_parser("run", help="Scaffold + rollout on the dummy sandbox")
    p_run.add_argument("recipe")
    p_run.add_argument("--dsn", default=None)
    p_run.add_argument("--measure")
    p_run.add_argument("--dimension")
    p_run.add_argument("--dimension2")
    p_run.add_argument("--value")
    p_run.add_argument("--year")
    p_run.add_argument("--n", type=int)
    p_run.add_argument("--threshold", type=float)
    p_run.add_argument("--min", dest="min_value", type=float)
    p_run.add_argument("--left")
    p_run.add_argument("--right")
    p_primo = an.add_parser("primitives", help="List the analytics primitive taxonomy (families + atoms)")
    p_primo.add_argument("--dsn", default=None)
    p_ch = an.add_parser("chain", help="Compose primitives into a RelOp plan (optional --rollout on dummy sandbox)")
    p_ch.add_argument("--dsn", default=None)
    p_ch.add_argument("--composite", help="Named composite from spec/analytics-primitives.json")
    p_ch.add_argument("--steps", help="JSON array of {op, ...binds}")
    p_ch.add_argument("--steps-file", dest="steps_file", help="Path to a JSON array of steps")
    p_ch.add_argument("--rollout", action="store_true", help="Execute the chain on the dummy sandbox")

    args = parser.parse_args(argv)
    workdir = Path(args.workdir)
    if args.cmd == "engines":
        engines = list_engines()
        print(f"{len(engines)} engines")
        for eng in engines:
            print(f"{eng.id:20} {eng.family:12} {eng.emit_family:12} tier={eng.execute_tier} {eng.description}")
        return 0
    if args.cmd == "slm":
        print(json.dumps(slm_status(), indent=2))
        return 0
    if args.cmd == "connect":
        rr = RevolveRelate.connect(args.dsn, workdir=workdir)
        print(json.dumps({"engine": rr.spec.engine.id, "family": rr.spec.engine.connection_family, "dsn": rr.spec.redacted_dsn}, indent=2))
        rr.close()
        return 0
    if args.cmd == "build":
        rr = RevolveRelate.connect(args.dsn, workdir=workdir)
        record = rr.build(refresh=args.refresh, rows_per_entity=args.rows)
        print(json.dumps(record, indent=2))
        rr.close()
        return 0
    if args.cmd == "ask":
        dsn = args.dsn or str(workdir / ".revolverelate" / "sandbox.sqlite")
        rr = RevolveRelate.connect(args.dsn, workdir=workdir) if args.dsn else RevolveRelate.connect(dsn, workdir=workdir)
        if not rr.cache.is_complete():
            if not args.dsn:
                print("build() has not completed; pass --dsn and run build first", file=sys.stderr)
                return 2
            rr.build()
        result = rr.ask(args.question)
        print(json.dumps({k: result[k] for k in result if k != "rows"}, indent=2, default=str))
        print(json.dumps(result.get("rows"), default=str))
        rr.close()
        return 0
    if args.cmd == "promote":
        ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
        rr = RevolveRelate.connect(args.dsn, workdir=workdir)
        if not rr.cache.is_complete():
            print("Refusing live push: build cache is not complete", file=sys.stderr)
            return 2
        result = rr.promote(ir, allow_live=args.allow_live)
        print(json.dumps(result, indent=2, default=str))
        rr.close()
        return 0
    if args.cmd == "sql":
        from revolverelate.compile.compiler import compile_ir

        ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
        sql, params = compile_ir(ir, args.engine)
        print(sql)
        print(json.dumps(params))
        return 0
    if args.cmd == "demo":
        from revolverelate.demo.http import serve as demo_serve

        return demo_serve(args.host, args.port, args.root)
    if args.cmd == "serve":
        from revolverelate.server.app import serve

        return serve(args.host, args.port, args.dsn, workdir)
    if args.cmd == "mcp":
        from revolverelate.mcp.server import main as mcp_main

        flags = []
        if args.install:
            flags.append("--install")
        if args.jsonl:
            flags.append("--jsonl")
        return mcp_main(flags)
    if args.cmd == "superstore":
        from revolverelate.samples.superstore import write_superstore

        dest = Path(args.dest) if args.dest else workdir / "superstore.sqlite"
        path = write_superstore(dest)
        print(path)
        return 0
    if args.cmd == "gene":
        from revolverelate.domain.gene import write_gene_pineal

        dest = Path(args.dest) if args.dest else workdir / "gene.sqlite"
        path = write_gene_pineal(dest)
        print(path)
        return 0
    if args.cmd == "automine":
        from revolverelate.domain.gene import write_gene_pineal

        dsn = args.dsn
        if not dsn:
            dest = Path(args.dest) if args.dest else workdir / "gene.sqlite"
            dsn = str(write_gene_pineal(dest))
        rr = RevolveRelate.connect(dsn, workdir=workdir)
        if not rr.cache.is_complete():
            rr.build()
        state = rr.automine(args.question, passes=args.passes, report=not args.no_report)
        print(json.dumps(state, indent=2, default=str))
        rr.close()
        return 0
    if args.cmd == "report":
        from revolverelate.domain.gene import write_gene_pineal
        from revolverelate.domain.research import run_research

        saved = workdir / ".revolverelate" / "automine.json"
        if saved.exists() and not args.dsn:
            report = run_research(json.loads(saved.read_text(encoding="utf-8")), workdir=workdir)
        else:
            dsn = args.dsn
            if not dsn:
                dest = Path(args.dest) if args.dest else workdir / "gene.sqlite"
                dsn = str(write_gene_pineal(dest))
            rr = RevolveRelate.connect(dsn, workdir=workdir)
            try:
                if not rr.cache.is_complete():
                    rr.build()
                report = rr.report(args.question or "what causes pinealblastoma")
            finally:
                rr.close()
        if args.markdown:
            print(report.get("markdown") or "")
        else:
            print(json.dumps(report, indent=2, default=str))
        return 0
    if args.cmd == "analytics":
        return _analytics_cmd(args, workdir)
    if args.cmd == "example":
        from revolverelate.samples.walkthrough import print_report, run_superstore_example

        dest = Path(args.dest) if args.dest else workdir / "superstore.sqlite"
        report = run_superstore_example(workdir, live_path=dest)
        print_report(report)
        return 0
    if args.cmd == "example-analytics":
        from revolverelate.samples.analytics_superstore import print_report as print_an
        from revolverelate.samples.analytics_superstore import run_superstore_analytics

        dest = Path(args.dest) if args.dest else workdir / "superstore.sqlite"
        report = run_superstore_analytics(workdir, live_path=dest)
        print_an(report)
        return 0
    if args.cmd == "tutorial":
        from revolverelate.samples.tutorial import print_tutorial, run_tutorial

        skip = {str(s).casefold() for s in (args.skip or [])}
        parts = tuple(p for p in ("superstore", "rag", "automine") if p not in skip)
        report = run_tutorial(Path(args.root), parts=parts, passes=args.passes)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print_tutorial(report)
        return 0
    if args.cmd == "rag":
        rr = RevolveRelate.connect(args.dsn, workdir=workdir)
        try:
            if not rr.cache.is_complete():
                rr.build()
            result = rr.rag(args.query, strategy=args.strategy, column=args.column, n=args.n)
            print(json.dumps(result, indent=2, default=str))
        finally:
            rr.close()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
