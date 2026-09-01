"""Deterministic NL → RelOp. SLM may fill residuals; this always produces algebra, never SQL."""

from __future__ import annotations

import re

from revolverelate.errors import AskError
from revolverelate.schema.model import Attribute, Entity, SchemaGraph

_MUTATE_INSERT = re.compile(r"\b(add|insert|create|new)\b", re.I)
_MUTATE_UPDATE = re.compile(r"\b(update|set|change|rename)\b", re.I)
_MUTATE_DELETE = re.compile(r"\b(delete|remove|drop)\b", re.I)
_NAMED = re.compile(r"\bnamed\s+([A-Za-z][A-Za-z0-9_-]*)", re.I)
_YEAR = re.compile(r"\b(20\d{2})\b")
_NUMBER = re.compile(
    r"\b(?:over|greater than|more than|above|>)\s+(\d+(?:\.\d+)?)",
    re.I,
)
_LT = re.compile(r"\b(?:less than|under|below|<)\s+(\d+(?:\.\d+)?)", re.I)
_EQ_NUM = re.compile(r"\b(?:quantity|qty)\s+(\d+(?:\.\d+)?)", re.I)
_CODE_PREFIX = re.compile(r"\b(ca|us)\s+orders\b", re.I)
_IN = re.compile(r"\bin\s+([A-Za-z][A-Za-z0-9_-]*)", re.I)
_REGION_WORDS = {"west", "east", "central", "south", "north"}

_SYNONYMS = {
    "sales": "orderline",
    "sale": "orderline",
    "lines": "orderline",
    "line": "orderline",
    "orderlines": "orderline",
    "orderline": "orderline",
    "customers": "customer",
    "customer": "customer",
    "orders": "orders",
    "order": "orders",
    "products": "product",
    "product": "product",
}

_MEASURE = {
    "sales": "sales",
    "sale": "sales",
    "profit": "profit",
    "quantity": "quantity",
    "qty": "quantity",
    "discount": "discount",
}

_AGG_FN = (
    (re.compile(r"\bcount\b", re.I), "count"),
    (re.compile(r"\bsum\b", re.I), "sum"),
    (re.compile(r"\baverage|avg\b", re.I), "avg"),
    (re.compile(r"\bmax(?:imum)?\b", re.I), "max"),
    (re.compile(r"\bmin(?:imum)?\b", re.I), "min"),
)


def tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9_]*", text or "")


def resolve_entities(question: str, graph: SchemaGraph) -> list[str]:
    found = []
    low = question.casefold()
    words = set(tokens(low))
    for word in list(words):
        mapped = _SYNONYMS.get(word)
        if mapped:
            words.add(mapped)
    for entity in graph.all_entities():
        key = entity.name.casefold()
        if key in low or key.rstrip("s") in words or key in words:
            found.append(entity.name)
            continue
        if any(_SYNONYMS.get(w) == key for w in words):
            found.append(entity.name)
    return found


def _eq(entity: str, attr: str, value) -> dict:
    return {
        "expr": "bin",
        "op": "=",
        "left": {"expr": "col", "entity": entity, "attr": attr},
        "right": {"expr": "lit", "value": value},
    }


def _bin(op: str, entity: str, attr: str, value) -> dict:
    return {
        "expr": "bin",
        "op": op,
        "left": {"expr": "col", "entity": entity, "attr": attr},
        "right": {"expr": "lit", "value": value},
    }


def _and(left: dict | None, right: dict | None) -> dict | None:
    if left and right:
        return {"expr": "bin", "op": "and", "left": left, "right": right}
    return left or right


def _or(left: dict, right: dict) -> dict:
    return {"expr": "bin", "op": "or", "left": left, "right": right}


def _not(inner: dict) -> dict:
    return {"expr": "un", "op": "not", "input": inner}


def _like(entity: str, attr: str, value: str) -> dict:
    return _bin("like", entity, attr, value)


def _col(entity: str, attr: str) -> dict:
    return {"expr": "col", "entity": entity, "attr": attr}


def _scan(entity: str) -> dict:
    return {"op": "scan", "entity": entity, "alias": entity}


def _value_catalog(graph: SchemaGraph) -> list[tuple[int, str, Entity, Attribute, str]]:
    catalog = []
    for entity in graph.all_entities():
        for attr in entity.attributes:
            if attr.sensitivity in {"critical", "pii"}:
                continue
            seen = set()
            for sample in attr.samples:
                key = sample.casefold()
                if key in seen:
                    continue
                seen.add(key)
                catalog.append((len(key), key, entity, attr, sample))
    catalog.sort(key=lambda row: (-row[0], row[1]))
    return catalog


def _measure_attr(graph: SchemaGraph, name: str) -> tuple[Entity, Attribute] | None:
    want = name.casefold()
    for entity in graph.all_entities():
        for attr in entity.attributes:
            if attr.name.casefold() == want:
                return entity, attr
    return None


def _attr_named(graph: SchemaGraph, *names: str) -> tuple[Entity, Attribute] | None:
    want = {n.casefold() for n in names}
    for entity in graph.all_entities():
        for attr in entity.attributes:
            if attr.name.casefold() in want:
                return entity, attr
    return None


def _collect_value_preds(question: str, graph: SchemaGraph) -> list[dict]:
    low = question.casefold()
    used: list[tuple[int, int]] = []
    preds: list[dict] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < b and end > a for a, b in used)

    for _n, key, entity, attr, sample in _value_catalog(graph):
        start = low.find(key)
        if start < 0:
            continue
        end = start + len(key)
        if overlaps(start, end):
            continue
        # skip tiny tokens that are also entity words
        if len(key) < 3:
            continue
        used.append((start, end))
        preds.append(_eq(entity.name, attr.name, sample))
    m_in = _IN.search(question)
    if m_in:
        token = m_in.group(1)
        already = any(
            token.casefold() in str((p.get("right") or {}).get("value", "")).casefold() for p in preds
        )
        if not already:
            prefer = "region" if token.casefold() in _REGION_WORDS else None
            picked = None
            if prefer:
                for entity in graph.all_entities():
                    attr = entity.attr(prefer)
                    if attr:
                        picked = (entity, attr, token.capitalize() if token.islower() else token)
                        break
            if picked is None:
                hits = list(graph.iter_sample_hits(token))
                if hits:
                    picked = (hits[0][0], hits[0][1], hits[0][2])
            if picked is None:
                filter_attrs = {
                    "country",
                    "city",
                    "state",
                    "region",
                    "segment",
                    "category",
                    "subcategory",
                    "shipmode",
                    "lastname",
                    "customername",
                    "productname",
                }
                for entity in graph.all_entities():
                    for attr in entity.attributes:
                        if attr.name.replace("_", "").casefold() in filter_attrs:
                            value = token.capitalize() if token.islower() else token
                            picked = (entity, attr, value)
                            break
                    if picked:
                        break
            if picked:
                preds.append(_eq(picked[0].name, picked[1].name, picked[2]))
    hit = _attr_named(graph, "CustomerName", "LastName", "Name")
    if hit and re.search(r"\b(named|customer)\b", question, re.I):
        for sample in hit[1].samples:
            first = sample.split()[0]
            if len(first) >= 3 and re.search(rf"\b{re.escape(first)}\b", question, re.I):
                if not any(
                    str((p.get("right") or {}).get("value", "")).casefold().startswith(first.casefold())
                    for p in preds
                ):
                    preds.append(_like(hit[0].name, hit[1].name, f"{first}%"))
                break
    return preds


def _collect_numeric_preds(question: str, graph: SchemaGraph) -> list[dict]:
    preds: list[dict] = []
    low = question.casefold()
    measure_name = None
    for word, mapped in _MEASURE.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            measure_name = mapped
            break
    if "high discount" in low:
        hit = _measure_attr(graph, "Discount")
        if hit:
            preds.append(_bin(">", hit[0].name, hit[1].name, 0))
            return preds
    if "zero discount" in low:
        hit = _measure_attr(graph, "Discount")
        if hit:
            preds.append(_bin("=", hit[0].name, hit[1].name, 0.0))
            return preds
    m_qty = _EQ_NUM.search(question)
    if m_qty:
        hit = _measure_attr(graph, "Quantity")
        if hit:
            preds.append(_eq(hit[0].name, hit[1].name, float(m_qty.group(1))))
            return preds
    m_over = _NUMBER.search(question)
    if m_over:
        hit = _measure_attr(graph, measure_name or "Sales")
        if hit:
            preds.append(_bin(">", hit[0].name, hit[1].name, float(m_over.group(1))))
    m_lt = _LT.search(question)
    if m_lt:
        hit = _measure_attr(graph, measure_name or "Profit")
        if hit:
            preds.append(_bin("<", hit[0].name, hit[1].name, float(m_lt.group(1))))
    return preds


def _collect_special_preds(question: str, graph: SchemaGraph) -> list[dict]:
    preds: list[dict] = []
    named = _NAMED.search(question)
    if named:
        hit = _attr_named(graph, "CustomerName", "LastName", "Name")
        if hit:
            preds.append(_like(hit[0].name, hit[1].name, f"{named.group(1)}%"))
    year = _YEAR.search(question)
    if year and re.search(r"\b(order|from|year)\b", question, re.I):
        hit = _attr_named(graph, "OrderDate")
        if hit:
            preds.append(_like(hit[0].name, hit[1].name, f"{year.group(1)}-%"))
    prefix = _CODE_PREFIX.search(question)
    if prefix:
        hit = _attr_named(graph, "OrderCode")
        if hit:
            preds.append(_like(hit[0].name, hit[1].name, f"{prefix.group(1).upper()}-%"))
    return preds


def _combine_preds(question: str, preds: list[dict]) -> dict | None:
    if not preds:
        return None
    if re.search(r"\bor\b", question, re.I) and len(preds) >= 2:
        tree = preds[0]
        for extra in preds[1:]:
            tree = _or(tree, extra)
        return tree
    tree = preds[0]
    for extra in preds[1:]:
        tree = _and(tree, extra)
    if re.search(r"\bnot\b", question, re.I) and len(preds) == 1:
        return _not(tree)
    return tree


def _entities_in_pred(pred: dict | None) -> list[str]:
    if not pred:
        return []
    names = []
    if pred.get("expr") == "col" and pred.get("entity"):
        names.append(pred["entity"])
    for key in ("left", "right", "input"):
        child = pred.get(key)
        if isinstance(child, dict):
            names.extend(_entities_in_pred(child))
    return names


def _join_from(root: str, needed: list[str], graph: SchemaGraph, *, join_type: str = "inner") -> dict:
    current = _scan(root)
    attached = {root.casefold()}
    for rel in graph.join_tree(root, needed):
        left_name = rel.from_entity
        right_name = rel.to_entity
        if right_name.casefold() in attached and left_name.casefold() not in attached:
            left_name, right_name = right_name, left_name
            on_left, on_right = rel.to_attrs[0], rel.from_attrs[0]
        else:
            on_left, on_right = rel.from_attrs[0], rel.to_attrs[0]
        current = {
            "op": "join",
            "joinType": join_type if join_type != "inner" else "inner",
            "left": current,
            "right": _scan(right_name),
            "on": [
                {
                    "expr": "bin",
                    "op": "=",
                    "left": _col(left_name, on_left),
                    "right": _col(right_name, on_right),
                }
            ],
        }
        attached.add(right_name.casefold())
        attached.add(left_name.casefold())
    return current


def _project_root(root: str, extras: list[str], graph: SchemaGraph, current: dict) -> dict:
    items = []
    root_ent = graph.require_entity(root)
    for attr in root_ent.attributes[:4]:
        items.append({"expr": _col(root, attr.name), "alias": attr.name})
    for extra in extras:
        ent = graph.entity(extra)
        if not ent or extra.casefold() == root.casefold():
            continue
        pk = ent.pk_attrs()
        attr = pk[0] if pk else ent.attributes[0]
        items.append({"expr": _col(extra, attr.name), "alias": attr.name})
    return {"op": "project", "items": items or [{"expr": {"expr": "star"}}], "input": current}


def _group_attr(question: str, graph: SchemaGraph) -> tuple[Entity, Attribute] | None:
    m = re.search(r"\bby\s+([A-Za-z][A-Za-z0-9_]*)", question, re.I)
    if not m:
        return None
    token = m.group(1)
    for entity in graph.all_entities():
        for attr in entity.attributes:
            if attr.name.casefold() == token.casefold():
                return entity, attr
    return None


def _try_aggregate(question: str, graph: SchemaGraph, ents: list[str]) -> dict | None:
    fn = None
    for rx, name in _AGG_FN:
        if rx.search(question):
            fn = name
            break
    if not fn:
        return None
    group = _group_attr(question, graph)
    measure = None
    low = question.casefold()
    for word, mapped in _MEASURE.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            measure = _measure_attr(graph, mapped)
            break
    root = ents[0] if ents else (measure[0].name if measure else graph.all_entities()[0].name)
    needed = list(ents[1:])
    if measure and measure[0].name.casefold() != root.casefold():
        needed.append(measure[0].name)
    if group and group[0].name.casefold() != root.casefold():
        needed.append(group[0].name)
    current = _join_from(root, needed, graph)
    if fn == "count":
        agg_expr = {"expr": "agg", "fn": "count", "input": {"expr": "star"}}
    else:
        target = measure or _measure_attr(graph, "Sales")
        if target is None:
            return None
        if target[0].name.casefold() not in {root.casefold(), *[n.casefold() for n in needed]}:
            current = _join_from(root, needed + [target[0].name], graph)
        agg_expr = {"expr": "agg", "fn": fn, "input": _col(target[0].name, target[1].name)}
    groups = [_col(group[0].name, group[1].name)] if group else []
    return {
        "kind": "query",
        "op": {
            "op": "aggregate",
            "groups": groups,
            "aggs": [{"expr": agg_expr, "alias": fn}],
            "input": current,
        },
    }


def _try_distinct(question: str, graph: SchemaGraph) -> dict | None:
    if not re.search(r"\bdistinct\b", question, re.I):
        return None
    low = question.casefold()
    for entity in graph.all_entities():
        for attr in entity.attributes:
            compact_q = re.sub(r"[^a-z0-9]", "", low)
            compact_a = attr.name.replace("_", "").casefold()
            stems = {compact_a, compact_a.rstrip("s"), attr.name.casefold(), attr.name.rstrip("s").casefold()}
            if compact_a.endswith("y"):
                stems.add(compact_a[:-1] + "ies")
            if compact_a + "s" != compact_a:
                stems.add(compact_a + "s")
            if any(stem and (stem in compact_q or stem in low) for stem in stems):
                return {
                    "kind": "query",
                    "op": {
                        "op": "distinct",
                        "input": {
                            "op": "project",
                            "items": [{"expr": _col(entity.name, attr.name), "alias": attr.name}],
                            "input": _scan(entity.name),
                        },
                    },
                }
    return None


def _try_union(question: str, graph: SchemaGraph, preds: list[dict]) -> dict | None:
    if "union" not in question.casefold() or len(preds) < 2:
        return None
    root = "Customer"
    if graph.entity(root) is None:
        root = graph.all_entities()[0].name
    pk = graph.require_entity(root).pk_attrs()
    attr = (pk[0].name if pk else graph.require_entity(root).attributes[0].name)

    def branch(pred: dict) -> dict:
        return {
            "op": "project",
            "items": [{"expr": _col(root, attr), "alias": "id"}],
            "input": {"op": "filter", "predicate": pred, "input": _scan(root)},
        }

    return {
        "kind": "query",
        "op": {"op": "setop", "set": "union", "all": False, "left": branch(preds[0]), "right": branch(preds[1])},
    }


def _try_top(question: str, graph: SchemaGraph) -> dict | None:
    if not re.search(r"\btop\b", question, re.I):
        return None
    hit = _measure_attr(graph, "Sales")
    if not hit:
        return None
    entity, attr = hit
    return {
        "kind": "query",
        "op": {
            "op": "limit",
            "count": 5,
            "input": {
                "op": "sort",
                "keys": [{"expr": _col(entity.name, attr.name), "direction": "DESC"}],
                "input": {
                    "op": "project",
                    "items": [{"expr": _col(entity.name, attr.name), "alias": attr.name}],
                    "input": _scan(entity.name),
                },
            },
        },
    }


def question_to_relop(question: str, graph: SchemaGraph) -> dict:
    q = (question or "").strip()
    if not q:
        raise AskError("Empty question")
    ents = resolve_entities(q, graph)
    if _MUTATE_INSERT.search(q):
        entity = ents[0] if ents else graph.all_entities()[0].name
        ent = graph.require_entity(entity)
        cols = [a.name for a in ent.attributes if not a.primary_key][:2]
        named = _NAMED.search(q)
        values = []
        for i, col in enumerate(cols):
            values.append(named.group(1) if named and i == 0 else f"dummy_{col}")
        return {
            "kind": "mutate",
            "op": {"op": "insert", "entity": entity, "columns": cols, "rows": [values]},
        }
    if _MUTATE_DELETE.search(q):
        entity = ents[0] if ents else graph.all_entities()[0].name
        return {"kind": "mutate", "op": {"op": "delete", "entity": entity}}
    if _MUTATE_UPDATE.search(q):
        entity = ents[0] if ents else graph.all_entities()[0].name
        ent = graph.require_entity(entity)
        writable = next((a.name for a in ent.attributes if not a.primary_key), None)
        if not writable:
            raise AskError(f"No writable column on {entity}")
        named = _NAMED.search(q)
        return {
            "kind": "mutate",
            "op": {
                "op": "update",
                "entity": entity,
                "set": {writable: named.group(1) if named else "updated"},
            },
        }

    value_preds = _collect_value_preds(q, graph)
    numeric_preds = _collect_numeric_preds(q, graph)
    special_preds = _collect_special_preds(q, graph)
    # "not in X" should keep a single value pred so _combine can negate it
    preds = value_preds + numeric_preds + special_preds
    if re.search(r"\bnot\b", q, re.I) and value_preds:
        preds = value_preds[:1]

    distinct = _try_distinct(q, graph)
    if distinct:
        return distinct
    union = _try_union(q, graph, value_preds)
    if union:
        return union
    top = _try_top(q, graph)
    if top:
        return top
    agg = _try_aggregate(q, graph, ents)
    if agg:
        return agg

    pred_ents = _entities_in_pred(_combine_preds(q, preds))
    if not ents:
        if pred_ents:
            ents = [pred_ents[0]]
        else:
            raise AskError("No schema entity mentioned; name a table from the provided schema")
    root = ents[0]
    needed = list(ents[1:])
    for name in pred_ents:
        if name.casefold() not in {root.casefold(), *[n.casefold() for n in needed]}:
            needed.append(name)
    join_type = "left" if re.search(r"\bleft join", q, re.I) else "inner"
    current = _join_from(root, needed, graph, join_type=join_type)
    pred = _combine_preds(q, preds)
    if pred:
        current = {"op": "filter", "predicate": pred, "input": current}
    projected = _project_root(root, needed, graph, current)
    return {"kind": "query", "op": {"op": "limit", "count": 50, "input": projected}}
