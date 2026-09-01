"""Tier A warehouse connection logic. Compile always works; execute needs the driver."""

from __future__ import annotations

from revolverelate.connection import ConnectionSpec
from revolverelate.errors import EngineError


def connect_warehouse(spec: ConnectionSpec):
    family = spec.engine.connection_family
    fn = {
        "snowflake": _snowflake,
        "bigquery": _bigquery,
        "databricks": _databricks,
        "trino": _trino,
        "redshift": _redshift,
        "tds": _tds,
    }.get(family)
    if fn is None:
        raise EngineError(
            f"{spec.engine.id} is compile-only (tier {spec.engine.execute_tier}, "
            f"family {family}). RelOp still compiles; live execute needs an adapter."
        )
    return fn(spec)


class _CursorAdapter:
    def __init__(self, spec: ConnectionSpec, conn, *, paramstyle: str = "qmark"):
        self.spec = spec
        self._conn = conn
        self.paramstyle = paramstyle

    def execute(self, sql: str, params=None):
        cur = self._conn.cursor()
        cur.execute(sql, list(params or []))
        desc = cur.description or []
        columns = [d[0] for d in desc]
        rows = [list(r) for r in (cur.fetchall() if columns else [])]
        return columns, rows

    def fetchall(self, sql: str, params=None):
        cur = self._conn.cursor()
        cur.execute(sql, list(params or []))
        return [tuple(r) for r in cur.fetchall()]

    def fetchone(self, sql: str, params=None):
        cur = self._conn.cursor()
        cur.execute(sql, list(params or []))
        row = cur.fetchone()
        return tuple(row) if row is not None else None

    def close(self) -> None:
        close = getattr(self._conn, "close", None)
        if close:
            close()


def _snowflake(spec: ConnectionSpec):
    try:
        import snowflake.connector
    except ImportError as exc:
        raise EngineError("Install revolverelate[snowflake] for Snowflake live execute") from exc
    q = spec.query
    conn = snowflake.connector.connect(
        account=q.get("account") or spec.host,
        user=spec.user,
        password=spec.password,
        warehouse=q.get("warehouse"),
        database=spec.database,
        schema=q.get("schema") or q.get("db_schema"),
        role=q.get("role"),
        authenticator=q.get("authenticator", "snowflake"),
    )
    return _CursorAdapter(spec, conn)


def _bigquery(spec: ConnectionSpec):
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise EngineError("Install revolverelate[bigquery] for BigQuery live execute") from exc
    client = bigquery.Client(project=spec.query.get("project") or spec.database)
    return _BigQueryAdapter(spec, client)


class _BigQueryAdapter:
    def __init__(self, spec, client):
        self.spec = spec
        self._conn = client

    def execute(self, sql: str, params=None):
        job = self._conn.query(sql)
        result = job.result()
        columns = [f.name for f in result.schema]
        rows = [list(r) for r in result]
        return columns, rows

    def fetchall(self, sql: str, params=None):
        _, rows = self.execute(sql, params)
        return [tuple(r) for r in rows]

    def fetchone(self, sql: str, params=None):
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        close = getattr(self._conn, "close", None)
        if close:
            close()


def _databricks(spec: ConnectionSpec):
    try:
        from databricks import sql
    except ImportError as exc:
        raise EngineError("Install revolverelate[databricks] for Databricks live execute") from exc
    conn = sql.connect(
        server_hostname=spec.host,
        http_path=spec.query.get("http_path"),
        access_token=spec.password or spec.query.get("token"),
    )
    return _CursorAdapter(spec, conn)


def _trino(spec: ConnectionSpec):
    if spec.engine.id == "athena":
        try:
            from pyathena import connect
        except ImportError as exc:
            raise EngineError("Install revolverelate[athena] for Athena live execute") from exc
        conn = connect(
            s3_staging_dir=spec.query.get("s3_staging_dir"),
            region_name=spec.query.get("region"),
            schema_name=spec.database or spec.query.get("schema"),
        )
        return _CursorAdapter(spec, conn)
    try:
        import trino
    except ImportError as exc:
        raise EngineError("Install revolverelate[trino] for Trino/Presto live execute") from exc
    conn = trino.dbapi.connect(
        host=spec.host,
        port=spec.port or 443,
        user=spec.user or "revolverelate",
        catalog=spec.query.get("catalog"),
        schema=spec.database or spec.query.get("schema"),
        http_scheme="https",
    )
    return _CursorAdapter(spec, conn)


def _redshift(spec: ConnectionSpec):
    try:
        import redshift_connector
    except ImportError:
        from revolverelate.adapters.sql import SqlAdapter

        return SqlAdapter(spec).connect()
    conn = redshift_connector.connect(
        host=spec.host,
        port=spec.port or 5439,
        database=spec.database,
        user=spec.user,
        password=spec.password,
    )
    return _CursorAdapter(spec, conn)


def _tds(spec: ConnectionSpec):
    try:
        import pyodbc
    except ImportError as exc:
        raise EngineError("Install revolverelate[mssql] for SQL Server / Synapse") from exc
    encrypt = spec.query.get("encrypt", "yes")
    conn = pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={spec.host},{spec.port or 1433};"
        f"DATABASE={spec.database};"
        f"UID={spec.user};PWD={spec.password};"
        f"Encrypt={encrypt};TrustServerCertificate=no"
    )
    return _CursorAdapter(spec, conn)
