"""Canonical schema graph — matches spec/schema-graph.schema.json."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


def _entity_from_row(row: dict) -> Entity:
    attrs = tuple(
        Attribute(
            name=a["name"],
            type=a.get("type", "TEXT"),
            nullable=a.get("nullable", True),
            primary_key=a.get("primaryKey") or a.get("primary_key") or False,
            unique=a.get("unique", False),
            samples=tuple(a.get("samples") or ()),
            comment=a.get("comment", ""),
            sensitivity=a.get("sensitivity", "public"),
        )
        for a in row.get("attributes") or []
    )
    return Entity(
        name=row["name"],
        schema_name=row.get("schema", "main"),
        kind=row.get("kind", "table"),
        attributes=attrs,
        comment=row.get("comment", ""),
    )


@dataclass(frozen=True)
class Attribute:
    name: str
    type: str = "TEXT"
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    samples: tuple[str, ...] = ()
    comment: str = ""
    sensitivity: str = "public"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "primaryKey": self.primary_key,
            "unique": self.unique,
            "samples": list(self.samples),
            "comment": self.comment,
            "sensitivity": self.sensitivity,
        }


@dataclass(frozen=True)
class Entity:
    name: str
    schema_name: str = "main"
    kind: str = "table"
    attributes: tuple[Attribute, ...] = ()
    comment: str = ""

    def attr(self, name: str) -> Attribute | None:
        key = name.casefold()
        for item in self.attributes:
            if item.name.casefold() == key:
                return item
        return None

    def pk_attrs(self) -> tuple[Attribute, ...]:
        return tuple(a for a in self.attributes if a.primary_key)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "schema": self.schema_name,
            "kind": self.kind,
            "comment": self.comment,
            "attributes": [a.to_dict() for a in self.attributes],
        }


@dataclass(frozen=True)
class Relationship:
    name: str
    from_entity: str
    from_attrs: tuple[str, ...]
    to_entity: str
    to_attrs: tuple[str, ...]
    kind: str = "foreign_key"
    cardinality: str = "n:1"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "from": self.from_entity,
            "fromAttrs": list(self.from_attrs),
            "to": self.to_entity,
            "toAttrs": list(self.to_attrs),
            "kind": self.kind,
            "cardinality": self.cardinality,
        }


@dataclass
class SchemaGraph:
    engine: str
    dialect: str = ""
    entities: dict[str, Entity] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    annotations: dict[str, Any] = field(default_factory=dict)
    domains: list[dict] = field(default_factory=list)
    constraints: list[dict] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    virtual: dict[str, Entity] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.dialect:
            self.dialect = self.engine

    def add_entity(self, entity: Entity) -> None:
        self.entities[entity.name.casefold()] = entity

    def add_virtual(self, entity: Entity) -> None:
        self.virtual[entity.name.casefold()] = entity

    def entity(self, name: str) -> Entity | None:
        key = name.casefold()
        return self.entities.get(key) or self.virtual.get(key)

    def require_entity(self, name: str) -> Entity:
        found = self.entity(name)
        if found is None:
            from revolverelate.errors import SchemaError

            raise SchemaError(f"Entity {name!r} is not in the built schema")
        return found

    def add_relationship(self, rel: Relationship) -> None:
        key = (
            rel.from_entity.casefold(),
            tuple(a.casefold() for a in rel.from_attrs),
            rel.to_entity.casefold(),
            tuple(a.casefold() for a in rel.to_attrs),
        )
        for existing in self.relationships:
            other = (
                existing.from_entity.casefold(),
                tuple(a.casefold() for a in existing.from_attrs),
                existing.to_entity.casefold(),
                tuple(a.casefold() for a in existing.to_attrs),
            )
            if other == key:
                return
        self.relationships.append(rel)

    def all_entities(self) -> list[Entity]:
        return sorted(self.entities.values(), key=lambda e: e.name.casefold())

    def neighbors(self) -> dict[str, list[Relationship]]:
        adj: dict[str, list[Relationship]] = defaultdict(list)
        for rel in self.relationships:
            adj[rel.from_entity.casefold()].append(rel)
            adj[rel.to_entity.casefold()].append(rel)
        return adj

    def shortest_path(self, start: str, goal: str) -> list[Relationship]:
        src = start.casefold()
        dst = goal.casefold()
        if src == dst:
            return []
        adj = self.neighbors()
        queue: deque[str] = deque([src])
        prev: dict[str, tuple[str, Relationship]] = {}
        seen = {src}
        while queue:
            node = queue.popleft()
            for rel in adj.get(node, []):
                nxt = (
                    rel.to_entity.casefold()
                    if rel.from_entity.casefold() == node
                    else rel.from_entity.casefold()
                )
                if nxt in seen:
                    continue
                seen.add(nxt)
                prev[nxt] = (node, rel)
                if nxt == dst:
                    queue.clear()
                    break
                queue.append(nxt)
        if dst not in prev:
            return []
        path: list[Relationship] = []
        cur = dst
        while cur != src:
            parent, rel = prev[cur]
            path.append(rel)
            cur = parent
        path.reverse()
        return path

    def join_tree(self, root: str, needed: Iterable[str]) -> list[Relationship]:
        want = {n.casefold() for n in needed if n.casefold() != root.casefold()}
        ordered: list[Relationship] = []
        seen_rel: set[tuple] = set()
        for target in want:
            for rel in self.shortest_path(root, target):
                ident = (
                    rel.from_entity.casefold(),
                    rel.from_attrs,
                    rel.to_entity.casefold(),
                    rel.to_attrs,
                    rel.kind,
                )
                if ident in seen_rel:
                    continue
                seen_rel.add(ident)
                ordered.append(rel)
        return ordered

    def freeze(self) -> SchemaGraph:
        return SchemaGraph(
            engine=self.engine,
            dialect=self.dialect,
            entities=dict(self.entities),
            relationships=list(self.relationships),
            notes=list(self.notes),
            annotations=dict(self.annotations),
            domains=list(self.domains),
            constraints=list(self.constraints),
            indexes=list(self.indexes),
            virtual=dict(self.virtual),
        )

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "dialect": self.dialect,
            "entities": [e.to_dict() for e in self.all_entities()],
            "relationships": [r.to_dict() for r in self.relationships],
            "domains": list(self.domains),
            "constraints": list(self.constraints),
            "indexes": list(self.indexes),
            "notes": list(self.notes),
            "annotations": dict(self.annotations),
            "virtualEntities": [e.to_dict() for e in self.virtual.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> SchemaGraph:
        graph = cls(engine=data.get("engine", "sqlite"), dialect=data.get("dialect", ""))
        for row in data.get("entities") or []:
            graph.add_entity(_entity_from_row(row))
        for row in data.get("virtualEntities") or data.get("annotations", {}).get("virtualEntities") or []:
            graph.add_virtual(_entity_from_row(row))
        for row in data.get("relationships") or []:
            graph.add_relationship(
                Relationship(
                    name=row["name"],
                    from_entity=row.get("from") or row.get("from_entity"),
                    from_attrs=tuple(row.get("fromAttrs") or row.get("from_attrs") or ()),
                    to_entity=row.get("to") or row.get("to_entity"),
                    to_attrs=tuple(row.get("toAttrs") or row.get("to_attrs") or ()),
                    kind=row.get("kind", "foreign_key"),
                    cardinality=row.get("cardinality", "n:1"),
                )
            )
        graph.notes = list(data.get("notes") or [])
        graph.annotations = dict(data.get("annotations") or {})
        graph.domains = list(data.get("domains") or [])
        graph.constraints = list(data.get("constraints") or [])
        graph.indexes = list(data.get("indexes") or [])
        return graph

    def iter_sample_hits(self, token: str) -> Iterator[tuple[Entity, Attribute, str]]:
        needle = token.casefold()
        for entity in self.entities.values():
            for attr in entity.attributes:
                for sample in attr.samples:
                    if sample.casefold() == needle:
                        yield entity, attr, sample
