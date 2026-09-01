"""Runnable tutorial: Superstore facts, overlay RAG chunks, then gene automine + report."""

from __future__ import annotations

import json
from pathlib import Path

from revolverelate.domain.gene import write_gene_pineal
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore
from revolverelate.vector.chunk import STRATEGIES, chunk_text
from revolverelate.vector.overlay import OVERLAY

_CAUSAL_DOC = (
    "Demand rose in the West. Sales fell because discounting was heavy. "
    "Therefore inventory piled up. After that, volume recovered."
)

_SEMANTIC_DOC = (
    "The bookcase holds office binders. Those shelves keep the same binders neat. "
    "Chairs fill the conference room. Seating around the conference table is tight."
)


def _clip_rows(rows, n: int = 5):
    return list(rows or [])[:n]


def run_superstore_part(root: Path) -> dict:
    live = write_superstore(root / "superstore.sqlite")
    workdir = root / "superstore-run"
    workdir.mkdir(parents=True, exist_ok=True)
    rr = RevolveRelate.connect(str(live), workdir=workdir)
    try:
        build = rr.build(rows_per_entity=6)
        asked = rr.ask("customers in West")
        recipe = rr.analytics.run("sum_by_dimension", measure="Sales", dimension="Region")
        promoted = rr.promote(asked["ir"])
        overlay = rr.overlay_stats()
        live_cols = [str(c) for c in (promoted.get("columns") or asked.get("columns") or [])]
        name_at = next((i for i, c in enumerate(live_cols) if "name" in c.casefold()), None)
        strategies = []
        try:
            strategies = [r[0] for r in rr.sandbox.execute(f'SELECT DISTINCT Strategy FROM "{OVERLAY}"')[1]]
        except Exception:
            strategies = []
        return {
            "part": "superstore",
            "live": str(live),
            "workdir": str(workdir),
            "build": build.get("status"),
            "entities": [e.name for e in rr.schema.all_entities()],
            "overlayChunks": overlay.get("chunks"),
            "textColumns": overlay.get("textColumns"),
            "chunkStrategies": sorted(strategies),
            "ask": {
                "question": "customers in West",
                "sql": asked.get("sql"),
                "sandboxRows": asked.get("rowCount") or len(asked.get("rows") or []),
                "liveRows": promoted.get("rowCount") or len(promoted.get("rows") or []),
                "liveNames": [
                    row[name_at] if name_at is not None and name_at < len(row) else next(
                        (cell for cell in row if isinstance(cell, str) and " " in cell and "@" not in cell),
                        row[1] if len(row) > 1 else row[0],
                    )
                    for row in (promoted.get("rows") or [])[:4]
                ],
            },
            "salesByRegion": {
                "status": recipe.get("status"),
                "rows": _clip_rows(recipe.get("rows")),
            },
        }
    finally:
        rr.close()


def run_rag_part(root: Path) -> dict:
    live = root / "superstore.sqlite"
    if not live.exists():
        write_superstore(live)
    workdir = root / "superstore-run"
    workdir.mkdir(parents=True, exist_ok=True)
    rr = RevolveRelate.connect(str(live), workdir=workdir)
    try:
        if not rr.cache.is_complete():
            rr.build(rows_per_entity=6)
        semantic = rr.rag("bookcase binders", strategy="semantic", column="ProductName", n=5)
        causal = rr.rag("sales fell because discounting", strategy="causal", column="ProductName", n=5)
        demos = {
            name: [
                {"text": row.get("text"), "cue": row.get("cue"), "role": row.get("role"), "level": row.get("level")}
                for row in chunk_text(doc, name)[:6]
            ]
            for name, doc in (("semantic", _SEMANTIC_DOC), ("causal", _CAUSAL_DOC), ("topic", _SEMANTIC_DOC), ("discourse", _CAUSAL_DOC), ("event", _CAUSAL_DOC))
        }
        return {
            "part": "rag",
            "overlayVirtual": OVERLAY,
            "strategies": sorted(STRATEGIES),
            "semantic": {
                "query": semantic.get("query"),
                "dummyRows": (semantic.get("relop") or {}).get("rowCount"),
                "liveRan": bool((semantic.get("live") or {}).get("ran")),
                "liveRows": (semantic.get("live") or {}).get("rowCount"),
                "sample": _clip_rows((semantic.get("live") or {}).get("rows") or (semantic.get("relop") or {}).get("rows")),
            },
            "causal": {
                "query": causal.get("query"),
                "dummyRows": (causal.get("relop") or {}).get("rowCount"),
                "liveRan": bool((causal.get("live") or {}).get("ran")),
                "sample": _clip_rows((causal.get("live") or {}).get("rows") or (causal.get("relop") or {}).get("rows")),
            },
            "chunkDemos": demos,
        }
    finally:
        rr.close()


def run_automine_part(root: Path, *, passes: int = 3) -> dict:
    live = write_gene_pineal(root / "gene.sqlite")
    workdir = root / "gene-run"
    workdir.mkdir(parents=True, exist_ok=True)
    rr = RevolveRelate.connect(str(live), workdir=workdir)
    try:
        if not rr.cache.is_complete():
            rr.build(rows_per_entity=4)
        state = rr.automine("what causes pinealblastoma", passes=passes)
        report = state.get("report") or {}
        return {
            "part": "automine",
            "live": str(live),
            "workdir": str(workdir),
            "question": state.get("question"),
            "passes": state.get("passes"),
            "stop": state.get("stop"),
            "identification": state.get("identification"),
            "conclusive": state.get("conclusive"),
            "candidates": state.get("candidates"),
            "etiologies": len(state.get("etiologies") or []),
            "mined": state.get("mined"),
            "reportTitle": report.get("title"),
            "citationCount": len(report.get("citations") or []),
            "reportPath": (report.get("paths") or {}).get("markdown"),
            "honesty": state.get("honesty"),
        }
    finally:
        rr.close()


def run_tutorial(
    root: str | Path,
    *,
    parts: tuple[str, ...] | None = None,
    passes: int = 3,
) -> dict:
    dest = Path(root)
    dest.mkdir(parents=True, exist_ok=True)
    wanted = parts if parts is not None else ("superstore", "rag", "automine")
    out: dict = {"kind": "tutorial", "root": str(dest), "parts": list(wanted)}
    if "superstore" in wanted:
        out["superstore"] = run_superstore_part(dest)
    if "rag" in wanted:
        out["rag"] = run_rag_part(dest)
    if "automine" in wanted:
        out["automine"] = run_automine_part(dest, passes=passes)
    (dest / "tutorial.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


def print_tutorial(report: dict) -> None:
    print("RevolveRelate tutorial")
    print("Ask in English. The engine fills RelOp, not SQL. Dummy sandbox first, then the same plan on live.")
    print()
    store = report.get("superstore")
    if store:
        print("1. Superstore (facts)")
        print(f"   Live file: {store['live']}")
        print(f"   Business tables: {', '.join(store.get('entities') or [])}")
        print(f"   OverlayChunk is virtual ({store.get('overlayChunks')} dummy chunks). Strategies: {', '.join(store.get('chunkStrategies') or [])}")
        ask = store.get("ask") or {}
        print(f"   Ask '{ask.get('question')}': {ask.get('sandboxRows')} dummy rows, then {ask.get('liveRows')} live rows.")
        names = ", ".join(str(n) for n in (ask.get('liveNames') or []) if n)
        if names:
            print(f"   Live names (not dummy): {names}")
        print()
    rag = report.get("rag")
    if rag:
        print("2. RAG and semantic chunks")
        print(f"   OverlayChunk is not a business table. Strategies in spec: {', '.join(rag.get('strategies') or [])}")
        sem = rag.get("semantic") or {}
        print(f"   Semantic retrieve '{sem.get('query')}': dummy {sem.get('dummyRows')} / live ran={sem.get('liveRan')}")
        cau = rag.get("causal") or {}
        print(f"   Causal retrieve '{cau.get('query')}': dummy {cau.get('dummyRows')} / live ran={cau.get('liveRan')}")
        demo = (rag.get("chunkDemos") or {}).get("causal") or []
        if demo:
            print("   Causal split of the West-discount note:")
            for row in demo[:4]:
                print(f"     [{row.get('role') or row.get('level')}] {row.get('cue') or '-'} {row.get('text')}")
        print()
    mine = report.get("automine")
    if mine:
        print("3. Autominer (possible etiologies, not proof)")
        print(f"   Question: {mine.get('question')}")
        print(f"   Passes: {mine.get('passes')}  stop: {mine.get('stop')}  identification: {mine.get('identification')}")
        print(f"   Candidates: {', '.join(mine.get('candidates') or []) or '(none)'}")
        print(f"   Mined catalog follow-ons: {', '.join(mine.get('mined') or []) or '(none)'}")
        print(f"   Report: {mine.get('reportTitle')} ({mine.get('citationCount')} citations)")
        if mine.get("reportPath"):
            print(f"   Markdown: {mine['reportPath']}")
        print(f"   {mine.get('honesty')}")
        print()
    print(f"Saved {Path(report.get('root') or '.') / 'tutorial.json'}")
