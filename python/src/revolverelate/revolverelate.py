"""Public API: connect → build (once, cached) → ask on dummy sandbox → promote live."""

from __future__ import annotations

import json
from pathlib import Path

from revolverelate.adapters.factory import make_adapter
from revolverelate.agent.promote import PromoteGate
from revolverelate.analytics.plans import AnalyticsLibrary
from revolverelate.buildcache import BuildCache
from revolverelate.compile.compiler import compile_ir
from revolverelate.connection import parse_dsn
from revolverelate.errors import PromoteError, SchemaError
from revolverelate.ir.nl import question_to_relop
from revolverelate.ir.validate import validate_ir
from revolverelate.policy.accept import accept_policy, default_policy
from revolverelate.policy.guard import assert_capability
from revolverelate.sandbox.engine import Sandbox
from revolverelate.schema.builder import build_schema
from revolverelate.schema.model import SchemaGraph
from revolverelate.slm.jobs import fill_relop, synthesize_policy


class Agent:
    def __init__(self, rr: RevolveRelate):
        self._rr = rr

    def mutate(self, question: str) -> dict:
        return self._rr.ask(question, expect="mutate")


class RevolveRelate:
    def __init__(self, spec, adapter, *, workdir: str | Path | None = None):
        self.spec = spec
        self.adapter = adapter
        self.workdir = Path(workdir or ".")
        self.cache = BuildCache(self.workdir)
        self._schema: SchemaGraph | None = None
        self._policy: dict | None = None
        self._sandbox: Sandbox | None = None
        self._validated: dict[str, bool] = {}
        self.agent = Agent(self)
        self.analytics = AnalyticsLibrary(self)

    @classmethod
    def connect(cls, dsn: str, *, workdir: str | Path | None = None, readonly: bool = False):
        spec = parse_dsn(dsn, readonly=readonly)
        adapter = make_adapter(spec, readonly=readonly)
        return cls(spec, adapter, workdir=workdir)

    @classmethod
    def from_schema(cls, graph: SchemaGraph, *, workdir: str | Path | None = None, dsn: str | None = None):
        """Build from a provided schema without requiring a live DSN first."""
        spec = parse_dsn(dsn or ":memory:")
        adapter = make_adapter(spec)
        inst = cls(spec, adapter, workdir=workdir)
        inst._schema = graph
        return inst

    def build(self, *, rows_per_entity: int = 8, refresh: bool = False, use_slm_policy: bool = False) -> dict:
        if self.cache.is_complete() and not refresh:
            return self._reload_cache()
        if refresh and self._sandbox is not None:
            self._sandbox.close()
            self._sandbox = None
        self.cache.begin(self.spec.engine.id)
        if self._schema is None or refresh:
            self._schema = build_schema(self.adapter)
        self.cache.save_graph(self._schema)
        if use_slm_policy:
            policy = synthesize_policy(self._schema)
        else:
            policy = accept_policy(default_policy(self._schema), self._schema)
        self._policy = policy
        self.cache.save_policy(policy)
        sandbox = Sandbox(self.cache.sandbox_path, self._schema, policy)
        sandbox.create(rows_per_entity=rows_per_entity)
        from revolverelate.vector.overlay import install_overlay

        install_overlay(sandbox, self._schema, policy)
        from revolverelate.analytics.asklog import install_asklog

        install_asklog(sandbox, self._schema)
        self.cache.save_graph(self._schema)
        self._sandbox = sandbox
        self.cache.mark(sandbox=True)
        record = self.cache.complete(
            engine=self.spec.engine.id,
            entities=len(self._schema.entities),
            relationships=len(self._schema.relationships),
        )
        return record

    def _reload_cache(self) -> dict:
        data = self.cache.require_complete()
        self._schema = SchemaGraph.from_dict(json.loads(self.cache.graph_path.read_text(encoding="utf-8")))
        self._policy = json.loads(self.cache.policy_path.read_text(encoding="utf-8"))
        self._sandbox = Sandbox(self.cache.sandbox_path, self._schema, self._policy).open()
        return data

    @property
    def schema(self) -> SchemaGraph:
        if self._schema is None:
            if self.cache.is_complete():
                self._reload_cache()
            else:
                raise SchemaError("Call build() once so the schema cache can be saved")
        return self._schema

    @property
    def policy(self) -> dict:
        if self._policy is None:
            if self.cache.is_complete():
                self._reload_cache()
            else:
                raise SchemaError("Call build() once so the policy cache can be saved")
        return self._policy

    @property
    def sandbox(self) -> Sandbox:
        if self._sandbox is None:
            if self.cache.is_complete():
                self._reload_cache()
            else:
                raise SchemaError("Call build() once so the dummy sandbox can be created")
        return self._sandbox

    def compile(self, ir: dict, engine: str | None = None) -> tuple[str, list]:
        validate_ir(ir, self.schema)
        return compile_ir(ir, engine or self.schema.engine)

    def ask(self, question: str, *, expect: str | None = None) -> dict:
        if not self.cache.is_complete():
            raise SchemaError("ask() runs on the dummy sandbox after a complete build() cache")
        assert_capability(self.policy, "read_sandbox" if expect != "mutate" else "mutate_sandbox")
        try:
            ir = fill_relop(question, self.schema, self.policy)
        except Exception:
            ir = question_to_relop(question, self.schema)
        if expect and ir.get("kind") != expect and expect == "mutate" and ir.get("kind") == "query":
            ir = question_to_relop(question, self.schema)
        return self.execute_ir(ir, expect=expect, question=question)

    def execute_ir(self, ir: dict, *, expect: str | None = None, question: str | None = None, composite: str | None = None) -> dict:
        """Run a RelOp on the dummy sandbox and save the validation ticket for promote."""
        if not self.cache.is_complete():
            raise SchemaError("execute_ir() runs on the dummy sandbox after a complete build() cache")
        assert_capability(self.policy, "read_sandbox" if expect != "mutate" else "mutate_sandbox")
        validate_ir(ir, self.schema)
        self.sandbox.begin()
        try:
            sql, params, columns, rows = self.sandbox.run_ir(ir)
            self.sandbox.commit()
            key = json.dumps(ir, sort_keys=True)
            self._validated[key] = True
            self._save_validation(key, ir, sql)
            try:
                from revolverelate.analytics.asklog import record_ask

                record_ask(
                    self.sandbox,
                    question=question or "",
                    ir=ir,
                    ticket=key[:48],
                    status="sandbox_ok",
                    target="sandbox",
                    composite=composite or "",
                    row_count=len(rows or []),
                )
            except Exception:
                pass
            return {
                "ir": ir,
                "sql": sql,
                "params": params,
                "columns": columns,
                "rows": rows,
                "target": "sandbox",
                "validated": True,
            }
        except Exception:
            self.sandbox.rollback()
            raise

    def promote(self, ir: dict, *, allow_live: bool = False) -> dict:
        self.cache.require_complete()
        key = json.dumps(ir, sort_keys=True)
        sandbox_ok = self._validated.get(key) or self._load_validation(key)
        gate = PromoteGate(self.cache, self.adapter, self.schema, self.policy)
        return gate.push(ir, sandbox_ok=bool(sandbox_ok), allow_live=allow_live)

    def replay_live(self, ir: dict | None = None, *, plan_id: str | None = None) -> dict:
        """Replay a dummy-ticketed RelOp on live. Overlay RelOps get a TEMP live chunk table first."""
        try:
            if plan_id:
                promoted = self.analytics.promote(plan_id)
                live = promoted.get("live") or {}
            elif ir:
                live = self.promote(ir)
            else:
                return {"ran": False, "error": "plan_id or ir required"}
            return {
                "ran": True,
                "target": "live",
                "sql": live.get("sql"),
                "columns": live.get("columns"),
                "rows": live.get("rows"),
                "rowCount": len(live.get("rows") or []),
            }
        except Exception as exc:
            return {"ran": False, "error": str(exc)[:240]}

    def overlay_stats(self) -> dict:
        from revolverelate.vector.overlay import OVERLAY, text_targets

        chunks = 0
        try:
            chunks = int(self.sandbox.execute(f'SELECT COUNT(*) FROM "{OVERLAY}"')[1][0][0])
        except Exception:
            chunks = 0
        fields = [a.name for a in (self.schema.entity(OVERLAY).attributes if self.schema.entity(OVERLAY) else [])]
        return {
            "entity": OVERLAY,
            "chunks": chunks,
            "fields": fields,
            "textColumns": [{"entity": e.name, "column": a.name} for e, a, _ in text_targets(self.schema)],
            "hint": "Dummy chunks non-PII text (and staging notes). Live promote rebuilds the same fields from live text only.",
        }

    def _save_validation(self, key: str, ir: dict, sql: str) -> None:
        path = self.cache.dir / "validated.json"
        data = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        data[key] = {"ir": ir, "sql": sql}
        path.write_text(json.dumps(data), encoding="utf-8")

    def _load_validation(self, key: str) -> bool:
        path = self.cache.dir / "validated.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return key in data

    def rag(self, query: str, *, strategy: str = "semantic", column: str = "ProductName", n: int = 5, live: bool = True) -> dict:
        """Semantic/causal retrieve: dummy overlay ticket, then the same RelOp on live text chunks."""
        from revolverelate.vector.chroma_store import rag

        if not self.cache.is_complete():
            raise SchemaError("rag() needs a complete build() cache")
        return rag(self, query, strategy=strategy, column=column, n=n, live=live)

    def _causal_chroma(self, query: str, *, column: str, n: int) -> tuple[list[dict], dict]:
        from revolverelate.vector.chroma_store import chroma_opted_in, chroma_status, query_chroma, sync_chroma

        if not chroma_opted_in():
            return [], {"available": False, "optedIn": False}
        chroma_hits: list[dict] = []
        status = chroma_status(self.workdir)
        if status.get("available"):
            if not status.get("count"):
                try:
                    sync_chroma(self.sandbox, self.workdir)
                    status = chroma_status(self.workdir)
                except Exception:
                    pass
            try:
                chroma_hits = query_chroma(self.workdir, query, strategy="causal", column=column, n=n)
            except Exception as exc:
                status = {**status, "queryError": str(exc)}
        return chroma_hits, status

    def causal(self, question: str, *, column: str = "ProductName", n: int = 8, explore: bool = False, live: bool = True) -> dict:
        """SLM CausalPlan → RelOp chain on dummy overlay. MiniLM knn is physical only."""
        from revolverelate.analytics.asklog import record_ask
        from revolverelate.analytics.causal_plan import score_causal_rows
        from revolverelate.slm.jobs import fill_causal_plan

        if explore:
            return self.causal_explore(question, column=column, n=n)
        if not self.cache.is_complete():
            raise SchemaError("causal() needs a complete build() cache")
        plan = fill_causal_plan(question, self.schema, self.policy)
        column = str((plan.get("goal") or {}).get("column") or column)
        ran = self.analytics.run_chain(plan["steps"], plan_id="causal-plan")
        score = score_causal_rows(ran.get("columns"), ran.get("rows"))
        try:
            record_ask(
                self.sandbox,
                question=plan.get("query") or question,
                objective=plan.get("query") or question,
                ir=ran.get("ir"),
                status=ran.get("status") or "sandbox_ok",
                composite=str(plan.get("composite") or ""),
                pattern="causal_plan",
                score=score,
                row_count=int(ran.get("rowCount") or 0),
            )
        except Exception:
            pass
        chroma_hits, status = self._causal_chroma(plan.get("query") or question, column=column, n=n)
        live_out = self.replay_live(plan_id=ran.get("id")) if live else {"ran": False}
        return {
            "kind": "causal_plan",
            "query": plan.get("query") or question,
            "goal": plan.get("goal"),
            "composite": plan.get("composite"),
            "steps": plan.get("steps"),
            "grammar": plan.get("grammar"),
            "relop": {
                "status": ran.get("status"),
                "sql": ran.get("sql"),
                "params": ran.get("params"),
                "columns": ran.get("columns"),
                "rows": ran.get("rows"),
                "rowCount": ran.get("rowCount"),
                "id": ran.get("id"),
                "target": "sandbox",
            },
            "live": live_out,
            "chroma": chroma_hits,
            "backend": status,
            "sandboxOnly": False,
            "overlayPromoted": bool(live_out.get("ran")),
            "hint": "Causal RelOp staged on dummy, then the same plan on live. Overlay fields rebuild from live non-PII text.",
        }

    def causal_explore(self, question: str, *, column: str = "ProductName", n: int = 8) -> dict:
        """Goal-scored abduce: run legal causal composites on dummy, AskLog, keep the winner."""
        from revolverelate.analytics.causal_plan import abduce_causal
        from revolverelate.slm.jobs import fill_causal_plan

        if not self.cache.is_complete():
            raise SchemaError("causal_explore() needs a complete build() cache")
        plan = fill_causal_plan(question, self.schema, self.policy)
        column = str((plan.get("goal") or {}).get("column") or column)
        out = abduce_causal(
            self,
            plan.get("query") or question,
            column=column,
            n=n,
            goal=plan.get("goal"),
            hinted=plan.get("composite"),
        )
        chroma_hits, status = self._causal_chroma(out.get("query") or question, column=column, n=n)
        out["chroma"] = chroma_hits
        out["backend"] = status
        out["live"] = self.replay_live(plan_id=(out.get("relop") or {}).get("id"))
        out["sandboxOnly"] = False
        out["overlayPromoted"] = bool((out.get("live") or {}).get("ran"))
        return out

    def automine(self, question: str, *, passes: int | None = None, until_stable: bool | None = None, live: bool = True, report: bool = True) -> dict:
        """Mine corpus → RelOp reflect → expand catalogued follow-ons → rebuild → mine again."""
        from revolverelate.domain.automine import run_automine

        if not self.cache.is_complete():
            self.build()
        return run_automine(self, question, passes=passes, until_stable=until_stable, live=live, report=report)

    def report(self, question: str | None = None, *, state: dict | None = None, use_slm: bool = True) -> dict:
        """Draft a citation-grounded report from automine findings (planner → researcher → reporter → validator)."""
        from revolverelate.domain.research import run_research

        payload = state
        if payload is None:
            path = Path(self.workdir) / ".revolverelate" / "automine.json"
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
            elif question:
                payload = self.automine(question, report=False)
            else:
                raise SchemaError("report() needs automine state, a saved automine.json, or a question")
        return run_research(payload, workdir=self.workdir, use_slm=use_slm)

    def kpi(self, kpi_id: str, *, live: bool = True) -> dict:
        """Run a domain KPI recipe on dummy, then the same RelOp on live."""
        from revolverelate.domain.kpi import run_kpi

        if not self.cache.is_complete():
            raise SchemaError("kpi() needs a complete build() cache")
        return run_kpi(self, kpi_id, live=live)

    def heuristic_cause(self, question: str, *, live: bool = True, discourse: bool = True) -> dict:
        """Because-clause bind + GLM odds-ratio on fact RelOp. Overlay is not promoted."""
        from revolverelate.analytics.heuristic import heuristic_cause

        if not self.cache.is_complete():
            raise SchemaError("heuristic_cause() needs a complete build() cache")
        return heuristic_cause(self, question, live=live, discourse=discourse)

    def pearl(self, question: str, *, live: bool = True, discourse: bool = False) -> dict:
        """Backdoor identify + dummy GLM/CASE, then the same fact RelOps on live. Overlay stays off live."""
        from revolverelate.analytics.pearl import pearl

        if not self.cache.is_complete():
            raise SchemaError("pearl() needs a complete build() cache")
        return pearl(self, question, live=live, discourse=discourse)

    def close(self) -> None:
        if self._sandbox:
            self._sandbox.close()
        if self.adapter:
            self.adapter.close()
