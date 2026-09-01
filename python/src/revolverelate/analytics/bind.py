"""Bind analytics slots to SchemaGraph primitives. Never emits SQL."""

from __future__ import annotations

from dataclasses import dataclass

from revolverelate.errors import AskError
from revolverelate.schema.model import Attribute, Entity, SchemaGraph

_DIM_NAMES = {
    "region",
    "segment",
    "category",
    "subcategory",
    "state",
    "city",
    "country",
    "shipmode",
    "status",
    "type",
    "name",
    "disease",
    "diagnosis",
    "gene",
    "symbol",
    "accession",
    "role",
    "alias",
    "drug",
    "cohort",
    "sex",
    "race",
}

_MEASURE_HINTS = {
    "sales",
    "profit",
    "quantity",
    "discount",
    "total",
    "amount",
    "price",
    "revenue",
    "cost",
    "qty",
    "cases",
    "rate",
    "risk",
    "incidence",
    "mortality",
    "count",
    "score",
    "exposure",
    "associationscore",
    "lofcount",
}

_TEXT_HINTS = (
    "abstract",
    "description",
    "title",
    "productname",
    "summary",
    "narrative",
    "note",
    "comment",
    "text",
    "body",
    "name",
    "evidence",
    "header",
    "sequence",
    "alias",
)

_DISEASE_TOKENS = {
    "pinealblastoma",
    "pineoblastoma",
    "pineal",
    "retinoblastoma",
    "disease",
    "syndrome",
}

_GENE_TOKENS = {
    "dicer1",
    "dicer",
    "rb1",
    "drosha",
    "dgcr8",
    "gene",
    "genes",
    "fasta",
    "symbol",
}


def is_numeric(attr: Attribute) -> bool:
    t = (attr.type or "").upper()
    return any(x in t for x in ("INT", "NUM", "DEC", "REAL", "FLOAT", "DOUBLE", "SERIAL"))


def is_measure(attr: Attribute) -> bool:
    if attr.primary_key or attr.sensitivity in {"critical", "pii"}:
        return False
    if attr.name.casefold().endswith("id"):
        return False
    return is_numeric(attr)


def is_dimension(attr: Attribute) -> bool:
    if attr.primary_key or attr.sensitivity in {"critical", "pii"}:
        return False
    if is_measure(attr):
        return False
    name = attr.name.replace("_", "").casefold()
    if name in _DIM_NAMES or name.endswith("name") or name.endswith("code"):
        return True
    t = (attr.type or "").upper()
    return "CHAR" in t or "TEXT" in t or "STR" in t or t == ""


def is_date(attr: Attribute) -> bool:
    t = (attr.type or "").upper()
    n = attr.name.casefold()
    return "DATE" in t or "TIME" in t or n.endswith("date") or n.endswith("at")


@dataclass(frozen=True)
class BoundCol:
    entity: Entity
    attr: Attribute

    @property
    def entity_name(self) -> str:
        return self.entity.name

    @property
    def attr_name(self) -> str:
        return self.attr.name


def resolve_column(graph: SchemaGraph, name: str, *, prefer: Entity | None = None) -> BoundCol:
    want = name.casefold()
    hits: list[BoundCol] = []
    for entity in graph.all_entities():
        for attr in entity.attributes:
            if attr.name.casefold() == want:
                hits.append(BoundCol(entity, attr))
    if not hits:
        raise AskError(f"No column {name!r} in the built schema")
    if prefer:
        for hit in hits:
            if hit.entity.name.casefold() == prefer.name.casefold():
                return hit
    if len(hits) == 1:
        return hits[0]
    for hit in hits:
        if is_measure(hit.attr) or is_dimension(hit.attr):
            return hit
    return hits[0]


def pick_fact(graph: SchemaGraph) -> Entity:
    scored = []
    for entity in graph.all_entities():
        score = sum(1 for a in entity.attributes if is_measure(a))
        score += sum(2 for r in graph.relationships if r.from_entity.casefold() == entity.name.casefold())
        scored.append((score, entity.name.casefold(), entity))
    scored.sort(reverse=True)
    if not scored:
        raise AskError("Schema has no entities")
    return scored[0][2]


def pick_measure(graph: SchemaGraph, name: str | None = None, *, fact: Entity | None = None) -> BoundCol:
    fact = fact or pick_fact(graph)
    if name:
        return resolve_column(graph, name, prefer=fact)
    hinted = []
    others = []
    for entity in graph.all_entities():
        for attr in entity.attributes:
            if not is_measure(attr):
                continue
            col = BoundCol(entity, attr)
            if attr.name.casefold() in _MEASURE_HINTS:
                hinted.append(col)
            else:
                others.append(col)
    for col in hinted + others:
        if col.entity.name.casefold() == fact.name.casefold():
            return col
    if hinted:
        return hinted[0]
    if others:
        return others[0]
    raise AskError("No numeric measure in the schema")


def pick_dimension(graph: SchemaGraph, name: str | None = None, *, avoid: BoundCol | None = None) -> BoundCol:
    if name:
        return resolve_column(graph, name)
    for entity in graph.all_entities():
        for attr in entity.attributes:
            if is_dimension(attr) and (avoid is None or attr.name.casefold() != avoid.attr.name.casefold()):
                return BoundCol(entity, attr)
    raise AskError("No dimension column in the schema")


def pick_date(graph: SchemaGraph, name: str | None = None) -> BoundCol | None:
    if name:
        return resolve_column(graph, name)
    for entity in graph.all_entities():
        for attr in entity.attributes:
            if is_date(attr):
                return BoundCol(entity, attr)
    return None


def list_measures(graph: SchemaGraph) -> list[str]:
    return [a.name for e in graph.all_entities() for a in e.attributes if is_measure(a)]


def list_dimensions(graph: SchemaGraph) -> list[str]:
    return [a.name for e in graph.all_entities() for a in e.attributes if is_dimension(a)]


def _tokens(text: str) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z0-9]+", (text or "").casefold()) if len(w) > 1}


def pick_text_column(graph: SchemaGraph, name: str | None = None) -> BoundCol:
    """Prefer abstract/name/description-style non-PII text for overlay + knn."""
    if name:
        return resolve_column(graph, name)
    ranked: list[tuple[int, BoundCol]] = []
    for entity in graph.all_entities():
        for attr in entity.attributes:
            if attr.primary_key or attr.sensitivity in {"critical", "pii"}:
                continue
            t = (attr.type or "").upper()
            if t and not any(x in t for x in ("CHAR", "TEXT", "STR", "CLOB", "JSON")):
                continue
            key = attr.name.replace("_", "").casefold()
            score = 0
            if key in {"abstract", "description", "title", "summary", "narrative", "text", "body", "evidence"}:
                score += 10
            elif key in {"header", "fasta"}:
                score += 4
            elif key in {"sequence", "seq"}:
                score += 1
            elif key in _TEXT_HINTS:
                score += 5
            elif key.endswith("name"):
                score += 2
            ranked.append((score, BoundCol(entity, attr)))
    ranked.sort(key=lambda row: row[0], reverse=True)
    if not ranked:
        raise AskError("No non-PII text column in the schema")
    return ranked[0][1]


def bind_analytics_goal(graph: SchemaGraph, question: str = "") -> dict:
    """Bind measure / dimension / text column / optional slice from any schema + English."""
    words = _tokens(question)
    measures = list_measures(graph)
    dims = list_dimensions(graph)
    measure = next((m for m in measures if m.casefold() in words), None)
    if measure is None:
        prefer_ms = ("sales", "cases", "profit", "discount", "quantity", "exposure")
        preferred = next((m for name in prefer_ms for m in measures if m.casefold() == name), None)
        if preferred is None:
            preferred = next((m for m in measures if m.casefold() in _MEASURE_HINTS), None)
        measure = preferred or pick_measure(graph).attr_name
    dimension = next((d for d in dims if d.replace("_", "").casefold() in words), None)
    if words & _DISEASE_TOKENS:
        disease_dim = next((d for d in dims if d.replace("_", "").casefold() in {"diseasename", "disease", "alias"}), None)
        if disease_dim:
            dimension = disease_dim
    elif words & _GENE_TOKENS:
        gene_dim = next((d for d in dims if d.replace("_", "").casefold() in {"symbol", "gene", "accession"}), None)
        if gene_dim:
            dimension = gene_dim
    if dimension is None:
        prefer = ("category", "diseasename", "disease", "symbol", "cohort", "region", "segment")
        preferred = next((d for name in prefer for d in dims if d.replace("_", "").casefold() == name), None)
        dimension = preferred or pick_dimension(graph).attr_name
    text_col = next((d for d in dims if d.replace("_", "").casefold() in words and d.casefold() != dimension.casefold()), None)
    if text_col is None and (words & _DISEASE_TOKENS):
        abstract = next((d for d in dims if d.replace("_", "").casefold() in {"abstract", "evidence", "summary"}), None)
        text_col = abstract
    if text_col is None:
        text_col = pick_text_column(graph).attr_name
    if words & {"fasta", "sequence", "header"}:
        fasta_col = next((d for d in dims if d.replace("_", "").casefold() in {"header", "sequence"}), None)
        if fasta_col:
            text_col = fasta_col
    treatments = [m for m in measures if m.casefold() != str(measure).casefold()]
    treatment = next(
        (
            m
            for m in treatments
            if m.casefold() in words or m.casefold() in {"discount", "exposure", "dose", "lofcount", "associationscore"}
        ),
        None,
    )
    if treatment is None:
        fact = pick_fact(graph)
        on_fact = []
        for name in treatments:
            try:
                if resolve_column(graph, name, prefer=fact).entity_name.casefold() == fact.name.casefold():
                    on_fact.append(name)
            except Exception:
                continue
        treatment = on_fact[0] if on_fact else (treatments[0] if treatments else measure)
    slice_ = {}
    if any(w in words for w in ("west", "east", "south", "north", "central")):
        region = next((d for d in dims if d.casefold() == "region"), None)
        if region:
            value = next(w for w in ("west", "east", "south", "north", "central") if w in words)
            slice_ = {"column": region, "value": value.title() if value != "central" else "Central"}
    return {
        "measure": measure,
        "dimension": dimension,
        "column": text_col,
        "treatment": treatment,
        "slice": slice_,
        "query": (question or "").strip(),
    }
