"""Live Superstore demo engine: browse live tables, ask on dummy, promote the same RelOp."""

from __future__ import annotations

import os
from pathlib import Path

from revolverelate.errors import PromoteError, SchemaError
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.analytics_superstore import CASES
from revolverelate.samples.superstore import example_questions, write_superstore

TABLES = ("Customer", "Product", "Orders", "OrderLine")

QUESTIONS = example_questions()

RECIPES = [
    {
        "id": case["id"],
        "title": case["use_case"],
        "recipe": case["recipe"],
        "args": case["args"],
    }
    for case in CASES
]

COMPOSITES = [
    {"id": "west_sales_by_category", "title": "West sales by category"},
    {"id": "loss_makers", "title": "Loss-making lines"},
    {"id": "share_then_cut", "title": "Share of total, then cut"},
    {"id": "quality_then_book", "title": "Quality profile, then the book"},
    {"id": "rag_then_agg", "title": "Nearest product chunks, then West sales", "sandboxOnly": True},
    {"id": "rag_semantic_knn", "title": "Semantic RAG (dummy overlay)", "sandboxOnly": True},
    {"id": "rag_causal_knn", "title": "Causal RAG (dummy overlay)", "sandboxOnly": True},
    {"id": "rag_causal_pair", "title": "Causal pairs (dummy overlay)", "sandboxOnly": True},
    {"id": "causal_then_agg", "title": "Causal pairs, then West sales", "sandboxOnly": True},
    {"id": "causal_then_intervene", "title": "Causes, then West discount do(0)", "sandboxOnly": True},
    {"id": "pearl_backdoor_facts", "title": "Pearl facts (Category-adjusted, live)"},
    {"id": "pearl_do_west", "title": "Pearl do(West discount=0) CASE, dummy then live"},
]

RAG_PRESETS = [
    {"query": "bookcase binders", "strategy": "semantic"},
    {"query": "conference chairs seating", "strategy": "semantic"},
    {"query": "sales fell because discounting", "strategy": "causal"},
]

CAUSAL_PRESETS = [
    {"question": "sales fell because discounting", "composite": "rag_causal_pair"},
    {"question": "what if West discount were zero after heavy discounting", "composite": "causal_then_intervene"},
]

PEARL_PRESETS = [
    {"question": "what if West discount were zero", "composite": "pearl_do_west"},
    {"question": "West sales fell because discounting", "composite": "pearl_backdoor_facts"},
]


def default_root() -> Path:
    env = os.environ.get("REVOLVERELATE_DEMO_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    if len(here.parents) > 4:
        repo = here.parents[4]
        if (repo / "python" / "src" / "revolverelate").exists():
            return repo / "demo" / "data"
    return Path.cwd() / "demo" / "data"


def _payload(columns: list[str], rows: list[list]) -> dict:
    return {
        "columns": columns,
        "rows": rows,
        "records": [dict(zip(columns, row)) for row in rows],
        "rowCount": len(rows),
    }


def _clip_result(result: dict, *, limit: int = 80) -> dict:
    rows = list(result.get("rows") or [])[:limit]
    columns = list(result.get("columns") or [])
    out = {
        "target": result.get("target"),
        "sql": result.get("sql"),
        "params": result.get("params"),
        "ir": result.get("ir"),
        "status": result.get("status"),
        "id": result.get("id"),
        **_payload(columns, rows),
    }
    live = result.get("live")
    if isinstance(live, dict):
        out["live"] = _clip_result(live, limit=limit)
    return out


class SuperstoreDemo:
    """One live Superstore file + dummy sandbox. RelOp only — never invents SQL."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.live = self.root / "superstore.sqlite"
        self.rr: RevolveRelate | None = None

    def boot(self, *, refresh: bool = False) -> dict:
        if not self.live.exists() or refresh:
            write_superstore(self.live)
        if self.rr is not None:
            self.rr.close()
        self.rr = RevolveRelate.connect(str(self.live), workdir=self.root)
        if not self.rr.cache.is_complete() or refresh:
            self.rr.build(refresh=refresh, rows_per_entity=8)
        return self.health()

    def _ready(self) -> RevolveRelate:
        if self.rr is None or not self.rr.cache.is_complete():
            self.boot()
        assert self.rr is not None
        return self.rr

    def health(self) -> dict:
        rr = self._ready()
        listed = rr.analytics.list() if rr.cache.is_complete() else {}
        return {
            "ok": True,
            "backend": "revolverelate-demo",
            "liveDb": str(self.live),
            "workdir": str(self.root),
            "complete": rr.cache.is_complete(),
            "engine": rr.spec.engine.id,
            "entities": [e.name for e in rr.schema.all_entities()],
            "measures": listed.get("measures") or [],
            "dimensions": listed.get("dimensions") or [],
            "hint": "Browse is live Superstore. Questions run on the dummy sandbox first; promote replays the same RelOp live.",
            "chroma": self.chroma(),
        }

    def catalog(self) -> dict:
        self._ready()
        return {
            "tables": list(TABLES),
            "questions": list(QUESTIONS),
            "recipes": RECIPES,
            "composites": COMPOSITES,
            "rag": RAG_PRESETS,
            "causal": CAUSAL_PRESETS,
            "pearl": PEARL_PRESETS,
        }

    def schema(self) -> dict:
        rr = self._ready()
        tables = []
        for entity in rr.schema.all_entities():
            columns, rows = rr.adapter.execute(f'SELECT COUNT(*) AS n FROM "{entity.name}"')
            count = rows[0][0] if rows else 0
            tables.append(
                {
                    "name": entity.name,
                    "count": count,
                    "columns": [
                        {
                            "name": attr.name,
                            "type": attr.type,
                            "pk": attr.primary_key,
                            "sensitivity": attr.sensitivity,
                        }
                        for attr in entity.attributes
                    ],
                }
            )
        return {
            "engine": rr.schema.engine,
            "relationships": [r.to_dict() for r in rr.schema.relationships],
            "tables": tables,
        }

    def table(self, name: str, *, limit: int = 200) -> dict:
        if name not in TABLES:
            raise SchemaError(f"Unknown Superstore table {name!r}. Known: {', '.join(TABLES)}")
        rr = self._ready()
        columns, rows = rr.adapter.execute(f'SELECT * FROM "{name}" LIMIT ?', [int(limit)])
        return {"name": name, "target": "live", **_payload(columns, rows)}

    def ask(self, question: str) -> dict:
        question = (question or "").strip()
        if not question:
            raise SchemaError("question is required")
        result = self._ready().ask(question)
        return {"mode": "ask", "question": question, "sandbox": _clip_result(result)}

    def promote(self, ir: dict) -> dict:
        if not isinstance(ir, dict):
            raise PromoteError("ir object required")
        result = self._ready().promote(ir)
        return {"mode": "promote", "live": _clip_result(result)}

    def question(self, question: str, *, promote: bool = True) -> dict:
        asked = self.ask(question)
        out = {**asked, "promoted": False}
        if promote:
            out["live"] = self.promote(asked["sandbox"]["ir"])["live"]
            out["promoted"] = True
        return out

    def recipe(self, recipe: str, *, promote: bool = True, **binds) -> dict:
        rr = self._ready()
        clean = {k: v for k, v in binds.items() if v is not None}
        plan = rr.analytics.run(str(recipe), **clean)
        out = {
            "mode": "recipe",
            "recipe": recipe,
            "args": clean,
            "sandbox": _clip_result(plan),
            "promoted": False,
        }
        if promote:
            live = rr.analytics.promote(plan["id"])
            out["live"] = _clip_result(live.get("live") or live)
            out["promoted"] = True
        return out

    def composite(self, name: str, *, promote: bool = True) -> dict:
        rr = self._ready()
        plan = rr.analytics.run_chain(composite=str(name))
        out = {
            "mode": "composite",
            "composite": name,
            "sandbox": _clip_result(plan),
            "promoted": False,
        }
        if promote:
            from revolverelate.analytics.primitives import get_composite

            try:
                sandbox_only = bool(get_composite(name).get("sandboxOnly"))
            except Exception:
                sandbox_only = False
            if sandbox_only:
                out["hint"] = "sandboxOnly composite — not promoted to live Superstore (no OverlayChunk on live)."
            else:
                live = rr.analytics.promote(plan["id"])
                out["live"] = _clip_result(live.get("live") or live)
                out["promoted"] = True
        return out

    def chroma(self) -> dict:
        from revolverelate.vector.chroma_store import chroma_available, chroma_status, sync_chroma

        if self.rr is None:
            return {"available": chroma_available()}
        status = chroma_status(self.rr.workdir)
        if status.get("available") and not status.get("count"):
            try:
                sync_chroma(self.rr.sandbox, self.rr.workdir)
                status = chroma_status(self.rr.workdir)
            except Exception as exc:
                status = {**status, "queryError": str(exc)}
        return status

    def rag(self, query: str, *, strategy: str = "semantic", column: str = "ProductName", n: int = 5) -> dict:
        rr = self._ready()
        result = rr.rag(query, strategy=strategy, column=column, n=int(n or 5))
        relop = result.get("relop") or {}
        chroma_rows = result.get("chroma") or []
        chroma_cols = list(chroma_rows[0].keys()) if chroma_rows else []
        return {
            "mode": "rag",
            "query": result.get("query"),
            "strategy": result.get("strategy"),
            "sandboxOnly": True,
            "promoted": False,
            "backend": result.get("backend"),
            "hint": result.get("hint"),
            "sandbox": _clip_result({**relop, "target": "sandbox"}),
            "chroma": {
                "columns": chroma_cols,
                "rows": [list(r.values()) for r in chroma_rows],
                "records": chroma_rows,
                "rowCount": len(chroma_rows),
                "target": "chroma-dummy",
            },
        }

    def causal(self, question: str, *, column: str = "ProductName", n: int = 8, explore: bool = False) -> dict:
        rr = self._ready()
        result = rr.causal(question, column=column, n=int(n or 8), explore=bool(explore))
        return self._causal_view(result)

    def causal_explore(self, question: str, *, column: str = "ProductName", n: int = 8) -> dict:
        rr = self._ready()
        return self._causal_view(rr.causal_explore(question, column=column, n=int(n or 8)))

    def pearl(self, question: str, *, live: bool = True) -> dict:
        rr = self._ready()
        result = rr.pearl(question, live=bool(live), discourse=False)
        sandbox_do = result.get("sandbox", {}).get("do") or {}
        live_do = result.get("live", {}).get("do") or {}
        live_facts = result.get("live", {}).get("facts") or {}
        return {
            "mode": "pearl",
            "query": result.get("query"),
            "bind": result.get("bind"),
            "identify": result.get("identify"),
            "sandboxAte": (result.get("sandbox") or {}).get("ate"),
            "sandboxGlm": (result.get("sandbox") or {}).get("glm"),
            "liveAte": live_facts.get("ate"),
            "liveGlm": live_facts.get("glm"),
            "overlayPromoted": False,
            "sandboxOnly": False,
            "promoted": bool(live_do.get("ran") or live_facts.get("ran")),
            "hint": result.get("hint"),
            "sandbox": _clip_result({**sandbox_do, "target": "sandbox"}),
            "live": _clip_result({**live_do, "target": "live"}) if live_do.get("ran") else {"target": "live", "columns": [], "rows": [], "records": [], "rowCount": 0},
        }

    def _causal_view(self, result: dict) -> dict:
        relop = result.get("relop") or {}
        chroma_rows = result.get("chroma") or []
        chroma_cols = list(chroma_rows[0].keys()) if chroma_rows else []
        kind = result.get("kind") or "causal_plan"
        return {
            "mode": "causal_explore" if kind == "causal_explore" else "causal",
            "query": result.get("query"),
            "goal": result.get("goal"),
            "composite": result.get("composite"),
            "hinted": result.get("hinted"),
            "steps": result.get("steps"),
            "grammar": result.get("grammar"),
            "winner": result.get("winner"),
            "candidates": result.get("candidates") or [],
            "memory": result.get("memory") or [],
            "sandboxOnly": True,
            "promoted": False,
            "backend": result.get("backend"),
            "hint": result.get("hint"),
            "sandbox": _clip_result({**relop, "target": "sandbox"}),
            "chroma": {
                "columns": chroma_cols,
                "rows": [list(r.values()) for r in chroma_rows],
                "records": chroma_rows,
                "rowCount": len(chroma_rows),
                "target": "chroma-dummy",
            },
        }

    def dispatch(self, op: str, payload: dict | None = None) -> dict:
        args = dict(payload or {})
        args.pop("op", None)
        args.pop("id", None)
        if op in {"boot", "health"}:
            return self.boot() if op == "boot" else self.health()
        if op == "catalog":
            return self.catalog()
        if op == "schema":
            return self.schema()
        if op == "table":
            return self.table(str(args.get("name") or args.get("table") or ""), limit=int(args.get("limit") or 200))
        if op == "ask":
            return self.ask(str(args.get("question") or ""))
        if op == "promote":
            return self.promote(args.get("ir") or {})
        if op == "question":
            return self.question(str(args.get("question") or ""), promote=bool(args.get("promote", True)))
        if op == "recipe":
            binds = {
                k: args[k]
                for k in ("measure", "dimension", "dimension2", "value", "year", "n", "threshold", "min", "left", "right")
                if args.get(k) is not None
            }
            binds.update(args.get("args") or {})
            return self.recipe(str(args.get("recipe") or ""), promote=bool(args.get("promote", True)), **binds)
        if op == "composite":
            return self.composite(str(args.get("composite") or args.get("name") or ""), promote=bool(args.get("promote", True)))
        if op == "chroma":
            return self.chroma()
        if op == "rag":
            return self.rag(
                str(args.get("query") or args.get("question") or ""),
                strategy=str(args.get("strategy") or "semantic"),
                column=str(args.get("column") or "ProductName"),
                n=int(args.get("n") or 5),
            )
        if op == "causal":
            return self.causal(
                str(args.get("question") or args.get("query") or ""),
                column=str(args.get("column") or "ProductName"),
                n=int(args.get("n") or 8),
                explore=bool(args.get("explore")),
            )
        if op == "causal_explore":
            return self.causal_explore(
                str(args.get("question") or args.get("query") or ""),
                column=str(args.get("column") or "ProductName"),
                n=int(args.get("n") or 8),
            )
        if op == "pearl":
            return self.pearl(
                str(args.get("question") or args.get("query") or ""),
                live=bool(args.get("live", True)),
            )
        raise SchemaError(f"unknown demo op {op!r}")

    def close(self) -> None:
        if self.rr is not None:
            self.rr.close()
            self.rr = None
