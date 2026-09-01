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

    p_fin = sub.add_parser("finance", help="Write an equities price-move SQLite sample (yfinance when installed, baked bars otherwise)")
    p_fin.add_argument("--dest", default=None, help="Output .sqlite path")
    p_fin.add_argument("--symbols", default=None, help="Comma-separated tickers (default: spec/domain-finance.json universe)")
    p_fin.add_argument("--period", default=None, help="yfinance period, e.g. 6mo, 1y, 2y")
    p_fin.add_argument("--offline", action="store_true", help="Skip yfinance and bake a seeded series")

    p_am = sub.add_parser("automine", help="Detect domain, RelOp-reflect, gate, remember evidence, expand catalogued follow-ons, mine again")
    p_am.add_argument("--dsn", default=None, help="Live corpus sqlite / DSN (default: write the domain sample)")
    p_am.add_argument("--question", default=None, help="Default: the detected domain's defaultQuestion")
    p_am.add_argument("--passes", type=int, default=3)
    p_am.add_argument("--domain", default=None, help="gene | finance (default: detect from the schema; when --dsn is omitted this picks the sample)")
    p_am.add_argument("--dest", default=None, help="When --dsn is omitted, write the domain sample here")
    p_am.add_argument("--offline", action="store_true", help="Finance sample: skip yfinance")
    p_am.add_argument("--rerun", action="store_true", help="Ignore a saved automine.json with the same reuse key")
    p_am.add_argument("--no-report", action="store_true", help="Skip the citation-grounded report after automine")

    p_rec = sub.add_parser("recall", help="knn over remembered automine evidence chunks (dummy overlay memory)")
    p_rec.add_argument("--dsn", required=True)
    p_rec.add_argument("--query", required=True)
    p_rec.add_argument("--n", type=int, default=5)
    p_rec.add_argument("--strategy", default="semantic", choices=["semantic", "causal"])

    p_rep = sub.add_parser("report", help="Draft a citation-grounded report from automine findings (local SLM or cloud API)")
    p_rep.add_argument("--dsn", default=None, help="Live corpus sqlite / DSN when automine has not been saved yet")
    p_rep.add_argument("--question", default=None, help="If no automine.json exists, run automine first")
    p_rep.add_argument("--passes", type=int, default=3)
    p_rep.add_argument("--dest", default=None, help="When --dsn is omitted, write the gene sample here")
    p_rep.add_argument("--markdown", action="store_true", help="Print report.md instead of JSON")

    p_auto = sub.add_parser("autonomy", help="Autonomy loop on atomic relations: seed, check, dummy rollout, score, mutate, replay the winner")
    p_auto.add_argument("--dsn", default=None, help="Live sqlite / DSN (default: write superstore.sqlite)")
    p_auto.add_argument("--objective", default=None, help="Goal in English. Omit (or pass --self) to let the engine form and test its own hypotheses first")
    p_auto.add_argument("--self", dest="self_directed", action="store_true", help="Self-directed: hypothesize, test, then search from the strongest supported hypothesis")
    p_auto.add_argument("--generations", type=int, default=None)
    p_auto.add_argument("--population", type=int, default=None)
    p_auto.add_argument("--rounds", type=int, default=None, help="Hypothesis rounds when self-directed (default 3)")
    p_auto.add_argument("--retest", action="store_true", help="Re-test hypotheses already in .revolverelate/hypotheses.json")
    p_auto.add_argument("--seed", type=int, default=7)
    p_auto.add_argument("--no-live", action="store_true", help="Do not replay the winner live")
    p_auto.add_argument("--dest", default=None, help="When --dsn is omitted, write the Superstore sample here")

    p_hyp = sub.add_parser("hypothesize", help="Self-directed hypothesis loop: survey the schema, form hypotheses, test (dummy ticket, live verdict), derive follow-ups, remember")
    p_hyp.add_argument("--dsn", default=None, help="Live sqlite / DSN (default: write superstore.sqlite; --domain finance writes the equities sample)")
    p_hyp.add_argument("--domain", default=None, help="Prefer a domain (gene, finance) when several match")
    p_hyp.add_argument("--rounds", type=int, default=None, help="Rounds (default 3, hard max 6)")
    p_hyp.add_argument("--per-round", type=int, default=None, help="Hypotheses tested per round (default 8)")
    p_hyp.add_argument("--retest", action="store_true", help="Re-test remembered hypotheses")
    p_hyp.add_argument("--no-search", action="store_true", help="Skip the atom search after supported hypotheses")
    p_hyp.add_argument("--no-live", action="store_true", help="Dummy only: verdicts are graded dummy_only and do not count as evidence")
    p_hyp.add_argument("--no-slm", action="store_true", help="Do not ask an SLM for extra bound hypotheses")
    p_hyp.add_argument("--dest", default=None, help="When --dsn is omitted, write the sample here")
    p_hyp.add_argument("--brief", action="store_true", help="Print one line per hypothesis instead of JSON")

    p_ex = sub.add_parser("example", help="Run the Superstore live walkthrough (connect, build, ask, promote)")
    p_ex.add_argument("--dest", default=None, help="Live Superstore sqlite path")

    p_exa = sub.add_parser("example-analytics", help="Run Superstore analytics recipes: scaffold → sandbox → live")
    p_exa.add_argument("--dest", default=None, help="Live Superstore sqlite path")

    p_tut = sub.add_parser("tutorial", help="Walk Superstore facts, overlay RAG chunks, gene automine + report, then finance price moves")
    p_tut.add_argument("--root", default="tutorial-run", help="Directory for sqlite files and .revolverelate caches")
    p_tut.add_argument("--skip", action="append", default=[], help="Skip a part: superstore, rag, automine, or finance (repeatable)")
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
    if args.cmd == "finance":
        from revolverelate.domain.finance import write_finance_equities

        dest = Path(args.dest) if args.dest else workdir / "equities.sqlite"
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
        path = write_finance_equities(dest, symbols=symbols, period=args.period, use_yfinance=not args.offline)
        print(path)
        return 0
    if args.cmd == "automine":
        dsn = args.dsn
        if not dsn:
            if (args.domain or "gene").casefold() == "finance":
                from revolverelate.domain.finance import write_finance_equities

                dest = Path(args.dest) if args.dest else workdir / "equities.sqlite"
                dsn = str(write_finance_equities(dest, use_yfinance=not args.offline))
            else:
                from revolverelate.domain.gene import write_gene_pineal

                dest = Path(args.dest) if args.dest else workdir / "gene.sqlite"
                dsn = str(write_gene_pineal(dest))
        rr = RevolveRelate.connect(dsn, workdir=workdir)
        if not rr.cache.is_complete():
            rr.build()
        state = rr.automine(
            args.question,
            passes=args.passes,
            report=not args.no_report,
            domain=args.domain,
            rerun=args.rerun,
        )
        print(json.dumps(state, indent=2, default=str))
        rr.close()
        return 0
    if args.cmd == "recall":
        rr = RevolveRelate.connect(args.dsn, workdir=workdir)
        try:
            if not rr.cache.is_complete():
                rr.build()
            print(json.dumps(rr.recall(args.query, n=args.n, strategy=args.strategy), indent=2, default=str))
        finally:
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
    if args.cmd == "autonomy":
        from revolverelate.samples.superstore import write_superstore

        dsn = args.dsn
        if not dsn:
            dest = Path(args.dest) if args.dest else workdir / "superstore.sqlite"
            dsn = str(write_superstore(dest))
        rr = RevolveRelate.connect(dsn, workdir=workdir)
        try:
            state = rr.autonomy(
                None if args.self_directed else args.objective,
                generations=args.generations,
                population=args.population,
                live=not args.no_live,
                seed=args.seed,
                rounds=args.rounds,
                retest=args.retest,
            )
        finally:
            rr.close()
        print(json.dumps(state, indent=2, default=str))
        return 0
    if args.cmd == "hypothesize":
        dsn = args.dsn
        if not dsn:
            if (args.domain or "").casefold() == "finance":
                from revolverelate.domain.finance import write_finance_equities

                dest = Path(args.dest) if args.dest else workdir / "finance.sqlite"
                dsn = str(write_finance_equities(dest, use_yfinance=False))
            elif (args.domain or "").casefold() == "gene":
                from revolverelate.domain.gene import write_gene_pineal

                dest = Path(args.dest) if args.dest else workdir / "gene.sqlite"
                dsn = str(write_gene_pineal(dest))
            else:
                from revolverelate.samples.superstore import write_superstore

                dest = Path(args.dest) if args.dest else workdir / "superstore.sqlite"
                dsn = str(write_superstore(dest))
        rr = RevolveRelate.connect(dsn, workdir=workdir)
        try:
            state = rr.hypothesize(
                rounds=args.rounds,
                per_round=args.per_round,
                live=not args.no_live,
                retest=args.retest,
                search=False if args.no_search else None,
                domain=args.domain,
                use_slm=not args.no_slm,
            )
        finally:
            rr.close()
        if args.brief:
            print(f"domain={state.get('domain')} fact={state['survey']['fact']} formed={state['formed']} derived={state['derived']} tested={len(state['tested'])} stop={state['stop']}")
            for row in state["tested"]:
                print(f"  r{row['round']} {row['verdict']:<12} {row['origin']:<24} {row['statement']}  | {row['why']}")
            for note in state.get("peerNotes") or []:
                print(f"  note: {note}")
            if state.get("search"):
                print(f"  search: {state['search']}")
            print(f"  {state['honesty']}")
        else:
            print(json.dumps(state, indent=2, default=str))
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
        parts = tuple(p for p in ("superstore", "rag", "automine", "finance") if p not in skip)
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
