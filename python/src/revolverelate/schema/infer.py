"""Infer relationships from naming conventions when catalogs omit FKs."""

from __future__ import annotations

import re

from revolverelate.schema.model import Relationship, SchemaGraph

_ID_SUFFIX = re.compile(r"(?i)(?:_id|id|[_]?no|[_]?key|[_]?code)$")


def infer_relationships(graph: SchemaGraph) -> None:
    entities = {e.name.casefold(): e for e in graph.all_entities()}
    stems: dict[str, str] = {}
    for name, entity in entities.items():
        stems[name] = entity.name
        stems[_singular(name)] = entity.name
        stems[_plural(name)] = entity.name

    for entity in graph.all_entities():
        pks = {a.name.casefold() for a in entity.pk_attrs()}
        for attr in entity.attributes:
            if attr.name.casefold() in pks:
                continue
            target = _match_target(attr.name, stems, entity.name)
            if not target:
                continue
            dest = graph.entity(target)
            if dest is None:
                continue
            dest_pk = dest.pk_attrs()
            to_attr = dest_pk[0].name if dest_pk else attr.name
            graph.add_relationship(
                Relationship(
                    name=f"{entity.name}.{attr.name}->{dest.name}",
                    from_entity=entity.name,
                    from_attrs=(attr.name,),
                    to_entity=dest.name,
                    to_attrs=(to_attr,),
                    kind="inferred",
                    cardinality="n:1",
                )
            )


def _match_target(col: str, stems: dict[str, str], self_name: str) -> str | None:
    raw = col.casefold()
    if raw in {"id", "pk", "rowid"}:
        return None
    stripped = _ID_SUFFIX.sub("", raw)
    if stripped in {"reportsto", "reports_to", "manager", "parent"}:
        return self_name
    for key in (raw, stripped, _plural(stripped), _singular(stripped)):
        if key in stems and stems[key].casefold() != self_name.casefold():
            return stems[key]
        if key + "s" in stems:
            return stems[key + "s"]
    return None


def _singular(name: str) -> str:
    n = name.casefold()
    if n.endswith("ies") and len(n) > 3:
        return n[:-3] + "y"
    if n.endswith("ses"):
        return n[:-2]
    if n.endswith("s") and not n.endswith("ss"):
        return n[:-1]
    return n


def _plural(name: str) -> str:
    n = name.casefold()
    if n.endswith("y") and n[-2:] not in {"ay", "ey", "oy", "uy"}:
        return n[:-1] + "ies"
    if n.endswith("s"):
        return n
    return n + "s"
