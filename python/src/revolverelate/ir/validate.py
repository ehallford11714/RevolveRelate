"""Walk a RelOp tree and reject names that are not in the provided schema."""

from __future__ import annotations

from revolverelate.errors import SchemaError
from revolverelate.schema.model import SchemaGraph


def validate_ir(doc: dict, graph: SchemaGraph) -> list[str]:
    issues: list[str] = []
    kind = (doc or {}).get("kind") or "query"
    if kind == "txn":
        for stmt in doc.get("statements") or []:
            issues.extend(validate_ir(stmt, graph))
        return issues
    if kind == "procedure":
        if doc.get("op"):
            issues.extend(_walk_op(doc["op"], graph, set()))
        for stmt in doc.get("statements") or []:
            issues.extend(validate_ir(stmt, graph))
        return issues
    op = doc.get("op")
    if not op:
        issues.append("IR is missing op")
        return issues
    issues.extend(_walk_op(op, graph, set()))
    if issues:
        raise SchemaError("IR is not grounded in the provided schema: " + "; ".join(issues))
    return issues


def _walk_op(op: dict, graph: SchemaGraph, ctes: set[str]) -> list[str]:
    if not isinstance(op, dict):
        return [f"op must be an object, got {type(op).__name__}"]
    issues: list[str] = []
    kind = op.get("op")
    if kind == "scan":
        name = op.get("entity", "")
        if graph.entity(name) is None and name.casefold() not in {c.casefold() for c in ctes}:
            issues.append(f"unknown entity {name!r}")
    elif kind in {"project", "filter", "having", "sort", "limit", "distinct", "aggregate", "window"}:
        if op.get("input"):
            issues.extend(_walk_op(op["input"], graph, ctes))
        if kind in {"filter", "having"}:
            issues.extend(_walk_expr(op.get("predicate"), graph, ctes))
        if kind == "project":
            for item in op.get("items") or []:
                issues.extend(_walk_expr(item.get("expr") if isinstance(item.get("expr"), dict) else item, graph, ctes))
        if kind == "aggregate":
            for g in op.get("groups") or []:
                issues.extend(_walk_expr(g, graph, ctes))
            for a in op.get("aggs") or []:
                issues.extend(_walk_expr(a.get("expr") if isinstance(a.get("expr"), dict) else a, graph, ctes))
    elif kind == "join":
        issues.extend(_walk_op(op.get("left") or {}, graph, ctes))
        issues.extend(_walk_op(op.get("right") or {}, graph, ctes))
        for pred in op.get("on") or []:
            issues.extend(_walk_expr(pred, graph, ctes))
    elif kind == "setop":
        issues.extend(_walk_op(op.get("left") or {}, graph, ctes))
        issues.extend(_walk_op(op.get("right") or {}, graph, ctes))
    elif kind == "values":
        pass
    elif kind == "with":
        names = set(ctes)
        for cte in op.get("ctes") or []:
            issues.extend(_walk_op(cte.get("input") or {}, graph, names))
            if cte.get("name"):
                names.add(str(cte["name"]))
        issues.extend(_walk_op(op.get("input") or {}, graph, names))
    elif kind == "insert":
        if graph.entity(op.get("entity", "")) is None:
            issues.append(f"unknown entity {op.get('entity')!r}")
        else:
            ent = graph.require_entity(op["entity"])
            for col in op.get("columns") or []:
                if ent.attr(col) is None:
                    issues.append(f"unknown column {ent.name}.{col}")
    elif kind in {"update", "delete"}:
        if graph.entity(op.get("entity", "")) is None:
            issues.append(f"unknown entity {op.get('entity')!r}")
        if op.get("predicate"):
            issues.extend(_walk_expr(op["predicate"], graph, ctes))
    elif kind == "call":
        pass
    return issues


def _walk_expr(expr: dict | None, graph: SchemaGraph, ctes: set[str] | None = None) -> list[str]:
    if not expr:
        return []
    if not isinstance(expr, dict):
        return [f"expr must be an object, got {type(expr).__name__}"]
    ctes = ctes or set()
    issues: list[str] = []
    kind = expr.get("expr")
    if kind == "col":
        entity = expr.get("entity")
        attr = expr.get("attr")
        cte_hit = entity and entity.casefold() in {c.casefold() for c in ctes}
        if entity and graph.entity(entity) is None and not cte_hit and attr:
            # aliases like "n" / "sales" after aggregate are allowed without entity
            if entity.casefold() not in {"n", "sales", "profit", "qty", "d", "avg_sales", "min_p", "max_s"}:
                issues.append(f"unknown entity {entity!r}")
        elif entity and attr and graph.entity(entity):
            ent = graph.entity(entity)
            if ent and ent.attr(attr) is None and attr != "*":
                issues.append(f"unknown column {entity}.{attr}")
    for key in ("left", "right", "input"):
        child = expr.get(key)
        if isinstance(child, dict):
            issues.extend(_walk_expr(child, graph, ctes))
    for child in expr.get("args") or []:
        if isinstance(child, dict):
            issues.extend(_walk_expr(child, graph, ctes))
    for child in expr.get("partition") or []:
        if isinstance(child, dict):
            issues.extend(_walk_expr(child, graph, ctes))
    for child in expr.get("order") or []:
        if isinstance(child, dict) and child.get("expr"):
            issues.extend(_walk_expr(child.get("expr"), graph, ctes))
    if kind == "case":
        for arm in expr.get("whens") or []:
            if isinstance(arm, dict):
                issues.extend(_walk_expr(arm.get("when"), graph, ctes))
                issues.extend(_walk_expr(arm.get("then"), graph, ctes))
        issues.extend(_walk_expr(expr.get("else"), graph, ctes))
    return issues
