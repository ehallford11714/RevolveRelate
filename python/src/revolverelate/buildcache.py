"""Saved build cache. Live push is illegal until a full build completes once."""

from __future__ import annotations

import json
import time
from pathlib import Path

from revolverelate.errors import PromoteError, SchemaError

CACHE_DIR = ".revolverelate"
BUILD_FILE = "build.json"
GRAPH_FILE = "schema.rrgraph.json"
POLICY_FILE = "policy.json"
SANDBOX_FILE = "sandbox.sqlite"


def cache_dir(root: str | Path) -> Path:
    return Path(root) / CACHE_DIR


class BuildCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.dir = cache_dir(self.root)

    @property
    def build_path(self) -> Path:
        return self.dir / BUILD_FILE

    @property
    def graph_path(self) -> Path:
        return self.dir / GRAPH_FILE

    @property
    def policy_path(self) -> Path:
        return self.dir / POLICY_FILE

    @property
    def sandbox_path(self) -> Path:
        return self.dir / SANDBOX_FILE

    def load(self) -> dict | None:
        if not self.build_path.exists():
            return None
        return json.loads(self.build_path.read_text(encoding="utf-8"))

    def is_complete(self) -> bool:
        data = self.load()
        if not data or data.get("status") != "complete":
            return False
        return (
            self.graph_path.exists()
            and self.policy_path.exists()
            and self.sandbox_path.exists()
            and bool(data.get("schema"))
            and bool(data.get("sandbox"))
            and bool(data.get("policy"))
        )

    def require_complete(self) -> dict:
        data = self.load()
        if data is None:
            raise PromoteError(
                "No saved build cache. Run build() once; live push is blocked until the "
                "dummy sandbox is created and validated."
            )
        if data.get("status") != "complete" or not self.is_complete():
            raise PromoteError(
                f"Build is not complete (status={data.get('status')!r}). "
                "Live query/push waits until schema, policy, and sandbox cache are all saved."
            )
        return data

    def begin(self, engine: str) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "status": "in_progress",
            "engine": engine,
            "startedAt": time.time(),
            "schema": False,
            "policy": False,
            "sandbox": False,
            "completedAt": None,
        }
        self._write(payload)
        return payload

    def mark(self, **flags) -> dict:
        data = self.load() or self.begin("unknown")
        data.update(flags)
        self._write(data)
        return data

    def complete(self, *, engine: str, entities: int, relationships: int) -> dict:
        data = self.load() or {}
        if not (
            data.get("schema")
            and data.get("policy")
            and data.get("sandbox")
            and self.graph_path.exists()
            and self.policy_path.exists()
            and self.sandbox_path.exists()
        ):
            data["status"] = "failed"
            self._write(data)
            raise SchemaError(
                "Cannot mark build complete: schema, policy, and sandbox files must all exist."
            )
        data.update(
            {
                "status": "complete",
                "engine": engine,
                "entities": entities,
                "relationships": relationships,
                "completedAt": time.time(),
            }
        )
        self._write(data)
        return data

    def save_graph(self, graph) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.graph_path.write_text(json.dumps(graph.to_dict(), indent=2), encoding="utf-8")
        self.mark(schema=True)

    def save_policy(self, policy: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(json.dumps(policy, indent=2), encoding="utf-8")
        self.mark(policy=True)

    def _write(self, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.build_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
