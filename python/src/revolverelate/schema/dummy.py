"""Schema-faithful dummy rows. Never copies live values for critical/pii columns."""

from __future__ import annotations

from collections import defaultdict

from revolverelate.schema.model import Attribute, Entity, SchemaGraph

DEFAULT_ROWS = 8


def topological_entities(graph: SchemaGraph) -> list[Entity]:
    children: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, int] = {e.name.casefold(): 0 for e in graph.all_entities()}
    for rel in graph.relationships:
        src = rel.from_entity.casefold()
        dst = rel.to_entity.casefold()
        if src == dst:
            continue
        if dst not in children[src]:
            children[src].add(dst)
            incoming[src] = incoming.get(src, 0) + 1
            incoming.setdefault(dst, 0)
    # parents (referenced) first
    ready = [k for k, n in incoming.items() if n == 0]
    ordered: list[str] = []
    seen: set[str] = set()
    while ready:
        node = ready.pop(0)
        if node in seen:
            continue
        seen.add(node)
        ordered.append(node)
        for rel in graph.relationships:
            if rel.to_entity.casefold() == node:
                child = rel.from_entity.casefold()
                incoming[child] = max(incoming.get(child, 1) - 1, 0)
                if incoming[child] == 0:
                    ready.append(child)
    for entity in graph.all_entities():
        if entity.name.casefold() not in seen:
            ordered.append(entity.name.casefold())
    return [graph.entities[k] for k in ordered if k in graph.entities]


def dummy_value(entity: Entity, attr: Attribute, index: int, *, masked: bool) -> object:
    if masked or attr.sensitivity in {"critical", "pii"}:
        return f"mask_{attr.name}_{index}"
    t = (attr.type or "TEXT").upper()
    if attr.primary_key and any(x in t for x in ("INT", "NUM", "DEC", "SERIAL")):
        return index
    if any(x in t for x in ("INT", "NUM", "DEC", "SERIAL", "REAL", "FLOAT", "DOUBLE")):
        if attr.samples:
            try:
                return type(0.0 if "REAL" in t or "FLOAT" in t else 0)(attr.samples[(index - 1) % len(attr.samples)])
            except (TypeError, ValueError):
                pass
        if attr.name.casefold() in {"sales", "profit", "quantity", "discount", "total"}:
            return float(100 * index) if "INT" not in t else index * 10
        return index
    if "BOOL" in t:
        return index % 2 == 0
    if any(x in t for x in ("DATE", "TIME")):
        return f"2024-01-{index:02d}"
    if attr.samples:
        return attr.samples[(index - 1) % len(attr.samples)]
    return f"{entity.name}_{attr.name}_{index}"


def generate_dummy_rows(
    graph: SchemaGraph,
    *,
    rows_per_entity: int = DEFAULT_ROWS,
    reveal: set[str] | None = None,
) -> dict[str, list[dict]]:
    reveal = reveal or set()
    data: dict[str, list[dict]] = {}
    for entity in topological_entities(graph):
        table: list[dict] = []
        fks = [r for r in graph.relationships if r.from_entity.casefold() == entity.name.casefold()]
        for i in range(1, rows_per_entity + 1):
            row: dict = {}
            for attr in entity.attributes:
                key = f"{entity.name}.{attr.name}".casefold()
                masked = attr.sensitivity in {"critical", "pii"} and key not in reveal
                row[attr.name] = dummy_value(entity, attr, i, masked=masked)
            for rel in fks:
                parent_rows = data.get(rel.to_entity.casefold()) or data.get(rel.to_entity)
                if not parent_rows:
                    continue
                parent = parent_rows[(i - 1) % len(parent_rows)]
                for src, dst in zip(rel.from_attrs, rel.to_attrs):
                    if dst in parent:
                        row[src] = parent[dst]
            table.append(row)
        data[entity.name] = table
        data[entity.name.casefold()] = table
    return data
