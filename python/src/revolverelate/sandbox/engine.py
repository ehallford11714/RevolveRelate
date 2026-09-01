"""Local duplicate DB: schema clone + dummy rows. Never a full live load."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from revolverelate.catalog import quote_ident
from revolverelate.compile.compiler import compile_ir
from revolverelate.errors import QueryError
from revolverelate.schema.dummy import generate_dummy_rows, topological_entities
from revolverelate.schema.model import SchemaGraph


def _sqlite_type(type_name: str) -> str:
    t = (type_name or "TEXT").upper()
    if any(x in t for x in ("INT", "SERIAL")):
        return "INTEGER"
    if any(x in t for x in ("REAL", "FLOAT", "DOUBLE", "DEC", "NUM")):
        return "REAL"
    if "BOOL" in t:
        return "INTEGER"
    return "TEXT"


class Sandbox:
    """Schema-faithful local sqlite (DuckDB when installed). Dummy data only."""

    def __init__(self, path: str | Path, graph: SchemaGraph, policy: dict):
        self.path = Path(path)
        self.graph = graph
        self.policy = policy
        self._conn: sqlite3.Connection | None = None
        self.backend = "sqlite"

    def create(self, *, rows_per_entity: int = 8) -> Sandbox:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_schema()
        reveal = {x.casefold() for x in self.policy.get("reveal") or []}
        dummy = generate_dummy_rows(self.graph, rows_per_entity=rows_per_entity, reveal=reveal)
        self._insert_dummy(dummy)
        self._conn.commit()
        return self

    def open(self) -> Sandbox:
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        return self

    def _create_schema(self) -> None:
        from revolverelate.catalog import get_engine

        engine = get_engine("sqlite")
        for entity in topological_entities(self.graph):
            cols = []
            for attr in entity.attributes:
                piece = f"{quote_ident(engine, attr.name)} {_sqlite_type(attr.type)}"
                if attr.primary_key:
                    piece += " PRIMARY KEY"
                elif not attr.nullable:
                    piece += " NOT NULL"
                cols.append(piece)
            sql = f"CREATE TABLE {quote_ident(engine, entity.name)} ({', '.join(cols)})"
            self._conn.execute(sql)
        for rel in self.graph.relationships:
            # FKs applied after tables exist; sqlite needs them in CREATE.
            # Recreate child tables is expensive; we enforce in dummy generation.
            continue

    def _insert_dummy(self, dummy: dict[str, list[dict]]) -> None:
        from revolverelate.catalog import get_engine

        engine = get_engine("sqlite")
        for entity in topological_entities(self.graph):
            rows = dummy.get(entity.name) or []
            if not rows:
                continue
            cols = [a.name for a in entity.attributes]
            qcols = ", ".join(quote_ident(engine, c) for c in cols)
            placeholders = ", ".join("?" * len(cols))
            sql = f"INSERT INTO {quote_ident(engine, entity.name)} ({qcols}) VALUES ({placeholders})"
            for row in rows:
                self._conn.execute(sql, [row.get(c) for c in cols])

    def execute(self, sql: str, params=None) -> tuple[list[str], list[list]]:
        if self._conn is None:
            raise QueryError("Sandbox is not open")
        try:
            cur = self._conn.execute(sql, list(params or []))
        except Exception as exc:  # noqa: BLE001
            raise QueryError(str(exc)) from exc
        desc = cur.description or []
        columns = [d[0] for d in desc]
        rows = [list(r) for r in cur.fetchall()] if columns else []
        return columns, rows

    def run_ir(self, ir: dict) -> tuple[str, list, list[str], list[list]]:
        sql, params = compile_ir(ir, "sqlite")
        columns, rows = self.execute(sql, params)
        return sql, params, columns, rows

    def begin(self) -> None:
        self._conn.execute("BEGIN")

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def table_names(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    def row_count(self, table: str) -> int:
        cur = self._conn.execute(f'SELECT COUNT(*) FROM "{table}"')
        return int(cur.fetchone()[0])

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
