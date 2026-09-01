"""RelOp constructors. Algebra only — never SQL."""

from __future__ import annotations


def col(entity: str, attr: str) -> dict:
    return {"expr": "col", "entity": entity, "attr": attr}


def attr_ref(attr: str) -> dict:
    return {"expr": "col", "attr": attr}


def lit(value) -> dict:
    return {"expr": "lit", "value": value}


def binop(op: str, left: dict, right: dict) -> dict:
    return {"expr": "bin", "op": op, "left": left, "right": right}


def item(expr: dict, alias: str | None = None) -> dict:
    row = {"expr": expr}
    if alias:
        row["alias"] = alias
    return row


def col_item(entity: str, attr: str, alias: str | None = None) -> dict:
    return item(col(entity, attr), alias or attr)


def scan(entity: str) -> dict:
    return {"op": "scan", "entity": entity, "alias": entity}


def project(inp: dict, *items: dict) -> dict:
    return {"op": "project", "items": list(items), "input": inp}


def filt(inp: dict, pred: dict) -> dict:
    return {"op": "filter", "predicate": pred, "input": inp}


def join(left: dict, right: dict, *ons: dict, join_type: str = "inner") -> dict:
    return {"op": "join", "joinType": join_type, "left": left, "right": right, "on": list(ons)}


def on_eq(a_ent: str, a_attr: str, b_ent: str, b_attr: str) -> dict:
    return binop("=", col(a_ent, a_attr), col(b_ent, b_attr))


def agg(inp: dict, groups: list, *aggs: dict) -> dict:
    return {"op": "aggregate", "groups": groups, "aggs": list(aggs), "input": inp}


def agg_fn(fn: str, inp: dict, alias: str) -> dict:
    return item({"expr": "agg", "fn": fn, "input": inp}, alias)


def count_star(alias: str = "n") -> dict:
    return item({"expr": "agg", "fn": "count", "input": {"expr": "star"}}, alias)


def sort(inp: dict, *keys: dict) -> dict:
    return {"op": "sort", "keys": list(keys), "input": inp}


def lim(inp: dict, n: int, offset: int | None = None) -> dict:
    op = {"op": "limit", "count": n, "input": inp}
    if offset:
        op["offset"] = offset
    return op


def distinct(inp: dict) -> dict:
    return {"op": "distinct", "input": inp}


def over(fn: str, *, inp: dict | None = None, partition: list | None = None, order: list | None = None) -> dict:
    expr = {"expr": "over", "fn": fn}
    if inp is not None:
        expr["input"] = inp
    if partition:
        expr["partition"] = partition
    if order:
        expr["order"] = order
    return expr


def query(op: dict) -> dict:
    return {"kind": "query", "op": op}


def case(whens: list, else_expr: dict | None = None) -> dict:
    row = {"expr": "case", "whens": list(whens)}
    if else_expr is not None:
        row["else"] = else_expr
    return row


def fn(name: str, *args: dict) -> dict:
    return {"expr": "fn", "fn": name, "args": list(args)}


def entities_in(op: dict | None) -> set[str]:
    """Scan entity names already on a RelOp tree (and CTE names)."""
    found: set[str] = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("op") == "scan" and node.get("entity"):
            found.add(str(node["entity"]).casefold())
        for key in ("input", "left", "right"):
            if node.get(key):
                walk(node[key])
        for cte in node.get("ctes") or []:
            if cte.get("name"):
                found.add(str(cte["name"]).casefold())
            walk(cte.get("input"))

    walk(op)
    return found


def join_entities(graph, root: str, needed: list[str], *, join_type: str = "inner") -> dict:
    """Walk SchemaGraph FKs from root to cover needed entities."""
    return attach_entities(graph, scan(root), needed, join_type=join_type, root=root)


def attach_entities(graph, current: dict, needed: list[str], *, join_type: str = "inner", root: str | None = None) -> dict:
    """Join missing entities onto an existing RelOp tree via SchemaGraph FKs."""
    attached = entities_in(current)
    still = [n for n in needed if n and n.casefold() not in attached]
    if not still:
        return current
    if root is None:
        for name in attached:
            ent = graph.entity(name)
            if ent is not None:
                root = ent.name
                break
    if root is None:
        root = still[0]
        current = scan(root)
        attached = {root.casefold()}
        still = [n for n in needed if n.casefold() not in attached]
    for rel in graph.join_tree(root, still):
        left_name = rel.from_entity
        right_name = rel.to_entity
        if right_name.casefold() in attached and left_name.casefold() not in attached:
            left_name, right_name = right_name, left_name
            on_left, on_right = rel.to_attrs[0], rel.from_attrs[0]
        else:
            on_left, on_right = rel.from_attrs[0], rel.to_attrs[0]
        if right_name.casefold() in attached:
            continue
        current = join(current, scan(right_name), on_eq(left_name, on_left, right_name, on_right), join_type=join_type)
        attached.add(left_name.casefold())
        attached.add(right_name.casefold())
    return current
