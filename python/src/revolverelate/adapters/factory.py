from revolverelate.adapters.sql import SqlAdapter
from revolverelate.adapters.warehouse import connect_warehouse
from revolverelate.connection import ConnectionSpec
from revolverelate.errors import EngineError

_SQL_LIVE = {
    "sqlite",
    "duckdb",
    "postgresql",
    "mysql",
    "mariadb",
    "cockroachdb",
    "neon",
    "supabase",
    "tidb",
    "aurora_postgres",
    "aurora_mysql",
    "planetscale",
    "alloydb",
    "citus",
    "timescaledb",
}


def make_adapter(spec: ConnectionSpec, *, readonly: bool = False):
    eid = spec.engine.id
    family = spec.engine.connection_family
    if eid == "duckdb" or eid == "motherduck":
        return _duckdb(spec)
    if eid in _SQL_LIVE or family in {"postgres", "mysql", "sqlite"}:
        return SqlAdapter(spec, readonly=readonly).connect()
    if family in {"snowflake", "bigquery", "databricks", "trino", "redshift", "tds"}:
        if spec.engine.execute_tier == "A" or eid in {
            "snowflake",
            "bigquery",
            "databricks",
            "trino",
            "athena",
            "redshift",
            "sqlserver",
        }:
            return connect_warehouse(spec)
    raise EngineError(
        f"{eid} compiles RelOp to {family} SQL (tier {spec.engine.execute_tier}) "
        "but has no live adapter yet. Use sandbox execute after build()."
    )


def _duckdb(spec: ConnectionSpec):
    try:
        import duckdb
    except ImportError:
        return SqlAdapter(spec).connect()
    path = spec.path or spec.database or ":memory:"
    conn = duckdb.connect(path)

    class _Duck:
        def __init__(self):
            self.spec = spec
            self._conn = conn

        def execute(self, sql, params=None):
            cur = conn.execute(sql, list(params or []))
            desc = cur.description or []
            columns = [d[0] for d in desc]
            rows = [list(r) for r in (cur.fetchall() if columns else [])]
            return columns, rows

        def fetchall(self, sql, params=None):
            return [tuple(r) for r in conn.execute(sql, list(params or [])).fetchall()]

        def fetchone(self, sql, params=None):
            row = conn.execute(sql, list(params or [])).fetchone()
            return tuple(row) if row is not None else None

        def close(self):
            conn.close()

    return _Duck()
