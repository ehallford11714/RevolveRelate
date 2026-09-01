"""Live introspection of tables, columns, keys, and declared foreign keys."""

from __future__ import annotations

from revolverelate.catalog import get_engine, quote_ident
from revolverelate.schema.model import Attribute, Entity, Relationship, SchemaGraph

_SKIP_TABLES = {"sqlite_sequence", "sqlite_stat1", "sqlite_stat4"}


def introspect(adapter) -> SchemaGraph:
    engine = adapter.spec.engine
    graph = SchemaGraph(engine=engine.id, dialect=engine.id)
    strategy = engine.introspect
    if strategy == "sqlite" or engine.id == "sqlite":
        _sqlite(adapter, graph)
    elif strategy in {
        "information_schema",
        "pg_catalog",
        "mysql",
        "mssql",
        "oracle",
        "vertica",
        "db2",
        "duckdb",
    }:
        _information_schema(adapter, graph)
    else:
        graph.notes.append(
            f"Live catalog introspection for {engine.id} uses family adapter."
        )
        if hasattr(adapter, "introspect"):
            adapter.introspect(graph)
    return graph


def _sqlite(adapter, graph: SchemaGraph) -> None:
    tables = adapter.fetchall(
        "SELECT name, type FROM sqlite_master "
        "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    engine = get_engine(graph.engine)
    for name, kind in tables:
        if name in _SKIP_TABLES:
            continue
        cols = adapter.fetchall(f"PRAGMA table_info({quote_ident(engine, name)})")
        attributes = []
        for row in cols:
            attributes.append(
                Attribute(
                    name=row[1],
                    type=row[2] or "TEXT",
                    nullable=not bool(row[3]),
                    primary_key=int(row[5] or 0) > 0,
                )
            )
        graph.add_entity(
            Entity(
                name=name,
                schema_name="main",
                kind="view" if str(kind).lower() == "view" else "table",
                attributes=tuple(attributes),
            )
        )
    for entity in list(graph.all_entities()):
        fks = adapter.fetchall(f"PRAGMA foreign_key_list({quote_ident(engine, entity.name)})")
        grouped: dict[int, list] = {}
        for row in fks:
            grouped.setdefault(int(row[0]), []).append(row)
        for _fid, rows in grouped.items():
            rows = sorted(rows, key=lambda r: int(r[1]))
            target = rows[0][2]
            src_cols = tuple(r[3] for r in rows)
            dest = graph.entity(str(target))
            dest_pk = dest.pk_attrs()[0].name if dest and dest.pk_attrs() else "id"
            dst_cols = tuple(r[4] or dest_pk for r in rows)
            graph.add_relationship(
                Relationship(
                    name=f"{entity.name}.{src_cols[0]}->{target}",
                    from_entity=entity.name,
                    from_attrs=src_cols,
                    to_entity=str(target),
                    to_attrs=dst_cols,
                    kind="foreign_key",
                    cardinality="n:1",
                )
            )


def _information_schema(adapter, graph: SchemaGraph) -> None:
    try:
        tables = adapter.fetchall(
            "SELECT table_schema, table_name, table_type "
            "FROM information_schema.tables "
            "WHERE table_schema NOT IN "
            "('information_schema','pg_catalog','sys','PERFORMANCE_SCHEMA','mysql')"
        )
    except Exception as exc:  # noqa: BLE001
        graph.notes.append(f"information_schema tables unavailable: {exc}")
        return
    for schema_name, table_name, table_type in tables:
        try:
            cols = adapter.fetchall(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? "
                "ORDER BY ordinal_position",
                [schema_name, table_name],
            )
        except Exception:
            continue
        attributes = [
            Attribute(
                name=row[0],
                type=str(row[1] or "TEXT"),
                nullable=str(row[2]).upper() in {"YES", "Y", "1", "TRUE"},
            )
            for row in cols
        ]
        graph.add_entity(
            Entity(
                name=str(table_name),
                schema_name=str(schema_name),
                kind="view" if "VIEW" in str(table_type).upper() else "table",
                attributes=tuple(attributes),
            )
        )
    try:
        rows = adapter.fetchall(
            "SELECT kcu.table_name, kcu.column_name, ccu.table_name, ccu.column_name "
            "FROM information_schema.referential_constraints rc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON kcu.constraint_name = rc.constraint_name "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON ccu.constraint_name = rc.unique_constraint_name"
        )
    except Exception:
        return
    for src_table, src_col, dst_table, dst_col in rows:
        graph.add_relationship(
            Relationship(
                name=f"{src_table}.{src_col}->{dst_table}",
                from_entity=str(src_table),
                from_attrs=(str(src_col),),
                to_entity=str(dst_table),
                to_attrs=(str(dst_col),),
                kind="foreign_key",
                cardinality="n:1",
            )
        )
