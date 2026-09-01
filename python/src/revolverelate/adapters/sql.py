"""SQL adapters: sqlite plus optional postgres/mysql drivers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from revolverelate.connection import ConnectionSpec
from revolverelate.errors import EngineError, QueryError

_PG = {
    "postgresql",
    "aurora_postgres",
    "cockroachdb",
    "yugabytedb",
    "citus",
    "timescaledb",
    "greenplum",
    "neon",
    "supabase",
    "alloydb",
    "materialize",
    "risingwave",
    "redshift",
    "pgvector",
}
_MY = {
    "mysql",
    "mariadb",
    "tidb",
    "aurora_mysql",
    "percona",
    "vitess",
    "planetscale",
    "singlestore",
}


def _cell(value):
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


class SqlAdapter:
    def __init__(self, spec: ConnectionSpec, *, readonly: bool = False):
        self.spec = spec
        self.readonly = readonly
        self._conn = None

    def connect(self) -> SqlAdapter:
        eid = self.spec.engine.id
        if eid == "sqlite":
            self._conn = self._sqlite()
        elif eid in _PG or self.spec.engine.connection_family in {"postgres", "redshift"}:
            self._conn = self._postgres()
        elif eid in _MY or self.spec.engine.connection_family == "mysql":
            self._conn = self._mysql()
        else:
            raise EngineError(
                f"{eid} is catalogued (tier {self.spec.engine.execute_tier}, "
                f"family {self.spec.engine.connection_family}) but this adapter "
                "handles sqlite / postgres-wire / mysql-wire. Use a warehouse adapter."
            )
        return self

    def _sqlite(self):
        path = self.spec.path or self.spec.database or ":memory:"
        if path != ":memory:" and not Path(path).exists() and self.readonly:
            raise EngineError(f"SQLite file not found: {path}")
        conn = sqlite3.connect(path if path == ":memory:" else str(Path(path)), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _postgres(self):
        try:
            import psycopg
        except ImportError as exc:
            raise EngineError("Install revolverelate[postgres] for PostgreSQL-wire engines") from exc
        return psycopg.connect(
            host=self.spec.host,
            port=self.spec.port or 5432,
            user=self.spec.user,
            password=self.spec.password,
            dbname=self.spec.database,
            autocommit=True,
            connect_timeout=10,
        )

    def _mysql(self):
        try:
            import pymysql
        except ImportError as exc:
            raise EngineError("Install revolverelate[mysql] for MySQL-wire engines") from exc
        return pymysql.connect(
            host=self.spec.host or "localhost",
            port=self.spec.port or 3306,
            user=self.spec.user,
            password=self.spec.password,
            database=self.spec.database,
            autocommit=True,
        )

    def _run(self, sql: str, params=None):
        params = list(params or [])
        if self.spec.engine.id == "sqlite":
            return self._conn.execute(sql, params)
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def execute(self, sql: str, params=None) -> tuple[list[str], list[list]]:
        try:
            cur = self._run(sql, params)
        except Exception as exc:  # noqa: BLE001
            raise QueryError(str(exc)) from exc
        desc = cur.description or []
        columns = [d[0] for d in desc]
        rows = [[_cell(v) for v in row] for row in (cur.fetchall() if columns else [])]
        return columns, rows

    def fetchall(self, sql: str, params=None) -> list[tuple]:
        cur = self._run(sql, params)
        return [tuple(r) for r in cur.fetchall()]

    def fetchone(self, sql: str, params=None):
        cur = self._run(sql, params)
        row = cur.fetchone()
        return tuple(row) if row is not None else None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
