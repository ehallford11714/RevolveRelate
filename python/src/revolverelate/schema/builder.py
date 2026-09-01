"""build_schema: introspect + inferred FKs + dummy-safe samples."""

from __future__ import annotations

from dataclasses import replace

from revolverelate.catalog import get_engine, quote_ident
from revolverelate.policy.classify import classify_attribute
from revolverelate.schema.infer import infer_relationships
from revolverelate.schema.introspect import introspect
from revolverelate.schema.model import SchemaGraph


def build_schema(adapter, *, infer: bool = True) -> SchemaGraph:
    graph = introspect(adapter)
    tagged = []
    for entity in graph.all_entities():
        attrs = tuple(
            replace(attr, sensitivity=classify_attribute(entity.name, attr.name, attr.type))
            for attr in entity.attributes
        )
        tagged.append(replace(entity, attributes=attrs))
    for entity in tagged:
        graph.add_entity(entity)
    if infer:
        infer_relationships(graph)
    collect_samples(adapter, graph)
    graph.notes.append(
        f"Built {len(graph.entities)} entities, {len(graph.relationships)} relationships "
        f"for {graph.engine}."
    )
    return graph.freeze()


def collect_samples(adapter, graph: SchemaGraph, *, max_distinct: int = 80) -> None:
    """Optional live samples. Skips critical/pii columns."""
    engine = get_engine(graph.engine)
    for entity in list(graph.all_entities()):
        updated = []
        for attr in entity.attributes:
            if attr.sensitivity in {"critical", "pii"}:
                updated.append(attr)
                continue
            t = (attr.type or "").upper()
            if any(x in t for x in ("INT", "NUM", "DEC", "REAL", "FLOAT", "BLOB")):
                updated.append(attr)
                continue
            qe = quote_ident(engine, entity.name)
            qa = quote_ident(engine, attr.name)
            try:
                rows = adapter.fetchall(
                    f"SELECT DISTINCT {qa} FROM {qe} WHERE {qa} IS NOT NULL LIMIT {max_distinct}"
                )
            except Exception:
                updated.append(attr)
                continue
            samples = tuple(str(r[0]) for r in rows if r[0] is not None)[:12]
            updated.append(replace(attr, samples=samples))
        graph.add_entity(replace(entity, attributes=tuple(updated)))
