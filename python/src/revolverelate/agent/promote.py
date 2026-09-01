"""Live push only after a complete saved build cache and sandbox validation."""

from __future__ import annotations

from revolverelate.buildcache import BuildCache
from revolverelate.compile.compiler import compile_ir
from revolverelate.errors import PromoteError
from revolverelate.ir.validate import validate_ir


class PromoteGate:
    def __init__(self, cache: BuildCache, adapter, graph, policy: dict):
        self.cache = cache
        self.adapter = adapter
        self.graph = graph
        self.policy = policy

    def push(self, ir: dict, *, sandbox_ok: bool, allow_live: bool = False) -> dict:
        self.cache.require_complete()
        if not sandbox_ok:
            raise PromoteError(
                "Sandbox validation has not passed. Run the RelOp against the dummy "
                "duplicate first; live push is blocked until that cache result is saved."
            )
        validate_ir(ir, self.graph)
        kind = ir.get("kind")
        if kind in {"mutate", "txn", "procedure"}:
            if not allow_live:
                raise PromoteError("Live mutate requires allow_live=True after the dummy sandbox passes")
            if "mutate_live" not in (self.policy.get("capabilities") or []):
                raise PromoteError("Policy does not grant mutate_live")
        # Queries that already passed the dummy sandbox may replay on live after a complete build.
        from revolverelate.vector.overlay import install_overlay_live, uses_overlay

        if uses_overlay(ir):
            install_overlay_live(self.adapter, self.graph, self.policy)
        sql, params = compile_ir(ir, self.graph.engine)
        columns, rows = self.adapter.execute(sql, params)
        return {"sql": sql, "params": params, "columns": columns, "rows": rows, "target": "live"}
