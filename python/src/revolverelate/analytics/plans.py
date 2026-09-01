"""Persist analytics plans: scaffold RelOp → sandbox rollout → live promote."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from revolverelate.analytics.bind import list_dimensions, list_measures
from revolverelate.analytics.catalog import list_recipes, scaffold_ir
from revolverelate.analytics.composites import check_chain, load_composite_rules
from revolverelate.analytics.primitives import (
    chain,
    get_composite,
    list_composites,
    list_families,
    list_primitives,
)
from revolverelate.domain.kpi import bind_kpis
from revolverelate.errors import PromoteError, SchemaError
from revolverelate.ir.validate import validate_ir


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "plan"


class AnalyticsLibrary:
    def __init__(self, rr):
        self._rr = rr

    @property
    def dir(self) -> Path:
        path = self._rr.cache.dir / "analytics"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list(self) -> dict:
        graph = self._rr.schema
        return {
            "families": list_families(),
            "primitives": [
                {"id": p["id"], "family": p["family"], "title": p["title"], "binds": list(p.get("binds") or [])}
                for p in list_primitives()
            ],
            "composites": [{"id": c["id"], "question": c.get("question"), "steps": list(c.get("steps") or [])} for c in list_composites()],
            "recipes": list_recipes(),
            "kpis": bind_kpis(graph),
            "measures": list_measures(graph),
            "dimensions": list_dimensions(graph),
            "plans": [p["id"] for p in self.plans()],
            "chainRules": {
                "depth": load_composite_rules()["depth"],
                "phases": [p["id"] for p in load_composite_rules()["phases"]],
                "patterns": [p["id"] for p in load_composite_rules()["patterns"]],
            },
        }

    def plans(self) -> list[dict]:
        rows = []
        for path in sorted(self.dir.glob("*.json")):
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        return rows

    def load(self, plan_id: str) -> dict:
        path = self.dir / f"{_slug(plan_id)}.json"
        if not path.exists():
            raise SchemaError(f"No analytics plan {plan_id!r}. Scaffold one first.")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, plan: dict) -> dict:
        path = self.dir / f"{plan['id']}.json"
        path.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")
        return plan

    def scaffold(self, recipe: str, **args) -> dict:
        if not self._rr.cache.is_complete():
            raise SchemaError("Scaffold needs a complete build() cache (dummy sandbox first).")
        clean = {k: v for k, v in args.items() if v is not None}
        ir = scaffold_ir(recipe, self._rr.schema, clean)
        validate_ir(ir, self._rr.schema)
        plan_id = _slug("-".join([recipe, *[str(v) for v in clean.values()]]))
        plan = {
            "id": plan_id,
            "recipe": recipe,
            "args": clean,
            "ir": ir,
            "status": "scaffolded",
            "target": "sandbox",
            "createdAt": time.time(),
        }
        return self._write(plan)

    def rollout(self, plan_id: str) -> dict:
        """Execute the scaffolded RelOp on the local duplicate only."""
        plan = self.load(plan_id)
        ran = self._rr.execute_ir(plan["ir"], question=plan.get("recipe") or plan.get("id"), composite=str(plan.get("recipe") or ""))
        plan.update(
            {
                "status": "sandbox_ok",
                "target": "sandbox",
                "sql": ran["sql"],
                "params": ran["params"],
                "columns": ran["columns"],
                "rowCount": len(ran["rows"]),
                "validatedAt": time.time(),
            }
        )
        self._write(plan)
        return {**plan, "rows": ran["rows"]}

    def promote(self, plan_id: str, *, allow_live: bool = False) -> dict:
        plan = self.load(plan_id)
        if plan.get("status") != "sandbox_ok":
            raise PromoteError(
                f"Plan {plan_id!r} has not rolled out on the dummy sandbox (status={plan.get('status')!r})."
            )
        live = self._rr.promote(plan["ir"], allow_live=allow_live)
        plan.update(
            {
                "status": "promoted",
                "target": "live",
                "liveRowCount": len(live.get("rows") or []),
                "promotedAt": time.time(),
            }
        )
        self._write(plan)
        return {**plan, "live": live}

    def run(self, recipe: str, **args) -> dict:
        """Scaffold + rollout in one step. Still sandbox-only."""
        plan = self.scaffold(recipe, **args)
        return self.rollout(plan["id"])

    def scaffold_chain(
        self,
        steps: list[dict] | None = None,
        *,
        composite: str | None = None,
        plan_id: str | None = None,
    ) -> dict:
        if not self._rr.cache.is_complete():
            raise SchemaError("Scaffold needs a complete build() cache (dummy sandbox first).")
        if composite:
            steps = list(get_composite(composite)["steps"]) + list(steps or [])
        if not steps:
            raise SchemaError("chain needs steps or a named composite")
        ir = chain(self._rr.schema, steps)
        validate_ir(ir, self._rr.schema)
        report = check_chain(steps)
        slug = _slug(plan_id or "-".join(["chain", composite or "", *[str(s.get("op") or "") for s in steps[:8]]]))
        plan = {
            "id": slug,
            "recipe": composite or "chain",
            "steps": steps,
            "chainCheck": report,
            "ir": ir,
            "status": "scaffolded",
            "target": "sandbox",
            "createdAt": time.time(),
        }
        return self._write(plan)

    def run_chain(self, steps: list[dict] | None = None, *, composite: str | None = None, plan_id: str | None = None) -> dict:
        plan = self.scaffold_chain(steps, composite=composite, plan_id=plan_id)
        return self.rollout(plan["id"])

    def socratic(self, objective: str) -> dict:
        """Objective → sub-questions as named composites. Each runs on the dummy."""
        from revolverelate.analytics.asklog import record_ask, score_rows
        from revolverelate.analytics.intent_apply import match_templates

        quests = match_templates(objective)
        ran = []
        for row in quests:
            plan = self.run_chain(composite=row["composite"])
            score = score_rows(plan.get("rows") or [])
            record_ask(
                self._rr.sandbox,
                question=row["question"],
                objective=objective,
                ir=plan.get("ir"),
                status=plan.get("status") or "sandbox_ok",
                composite=row["composite"],
                pattern="socratic",
                score=score,
                row_count=int(plan.get("rowCount") or 0),
            )
            ran.append({**row, "status": plan.get("status"), "rowCount": plan.get("rowCount"), "score": score})
        return {"objective": objective, "kind": "socratic", "steps": ran}

    def ideate(self, objective: str) -> dict:
        """Enumerate legal RelOp candidates, sandbox-score them (LINX-style ADE)."""
        from revolverelate.analytics.asklog import record_ask, score_rows
        from revolverelate.analytics.intent_apply import ideate_candidates
        from revolverelate.analytics.primitives import get_composite

        ranked = []
        for cid in ideate_candidates(objective)[:5]:
            spec = get_composite(cid)
            plan = self.run_chain(composite=cid)
            score = score_rows(plan.get("rows") or [])
            record_ask(
                self._rr.sandbox,
                question=spec.get("question") or cid,
                objective=objective,
                ir=plan.get("ir"),
                status=plan.get("status") or "sandbox_ok",
                composite=cid,
                pattern=str(spec.get("pattern") or "ideate"),
                score=score,
                row_count=int(plan.get("rowCount") or 0),
            )
            ranked.append(
                {
                    "composite": cid,
                    "question": spec.get("question"),
                    "sandboxOnly": bool(spec.get("sandboxOnly")),
                    "status": plan.get("status"),
                    "score": score,
                    "rowCount": plan.get("rowCount"),
                }
            )
        ranked.sort(key=lambda r: r["score"], reverse=True)
        return {"objective": objective, "kind": "ideate", "candidates": ranked}

    def abduce(self, objective: str) -> dict:
        """Search legal thoughts; dummy scores them; the matched template wins."""
        from revolverelate.analytics.intent_apply import match_composite

        explored = self.ideate(objective)
        preferred = match_composite(objective)
        winner = next((row for row in explored.get("candidates") or [] if row.get("composite") == preferred), None)
        if winner is None:
            winner = (explored.get("candidates") or [{}])[0]
        return {"objective": objective, "kind": "abduce", "winner": winner, "candidates": explored.get("candidates")}

    def explore(self, objective: str) -> dict:
        """Socratic decompose, then ideate, then abduce a winner."""
        quest = self.socratic(objective)
        ideas = self.ideate(objective)
        winner = (ideas.get("candidates") or [{}])[0]
        return {"objective": objective, "kind": "explore", "socratic": quest.get("steps"), "candidates": ideas.get("candidates"), "winner": winner}

    def rag(self, query: str, *, strategy: str = "semantic", column: str = "ProductName", n: int = 5, live: bool = True) -> dict:
        return self._rr.rag(query, strategy=strategy, column=column, n=n, live=live)

    def causal(self, question: str, *, column: str = "ProductName", n: int = 8, explore: bool = False, live: bool = True) -> dict:
        return self._rr.causal(question, column=column, n=n, explore=explore, live=live)

    def causal_explore(self, question: str, *, column: str = "ProductName", n: int = 8) -> dict:
        return self._rr.causal_explore(question, column=column, n=n)

    def heuristic_cause(self, question: str, *, live: bool = True, discourse: bool = True) -> dict:
        return self._rr.heuristic_cause(question, live=live, discourse=discourse)

    def pearl(self, question: str, *, live: bool = True, discourse: bool = False) -> dict:
        return self._rr.pearl(question, live=live, discourse=discourse)

    def hypothesize(self, *, composite: str | None = None, steps: list[dict] | None = None, name: str = "Hypothesis") -> dict:
        """Name a RelOp as a dummy CTE view. Virtual. Not a live table."""
        from revolverelate.analytics.primitives import apply_primitive, chain
        from revolverelate.ir.rel import query
        from revolverelate.schema.model import Attribute, Entity

        if composite or steps:
            plan = self.scaffold_chain(steps, composite=composite)
            inner = plan["ir"].get("op") or plan["ir"]
        else:
            inner = apply_primitive(self._rr.schema, "scan_fact", None, {})
        wrapped = apply_primitive(self._rr.schema, "hypothesize", inner, {"name": name})
        ir = query(wrapped)
        ran = self._rr.execute_ir(ir, question=f"hypothesize {name}", composite=composite or "hypothesize")
        graph = self._rr.schema
        if graph.entity(name) is None:
            graph.add_virtual(
                Entity(
                    name=name,
                    kind="overlay",
                    comment="Named dummy RelOp view",
                    attributes=(Attribute("value", "TEXT"),),
                )
            )
        return {**ran, "name": name, "status": "sandbox_ok"}
