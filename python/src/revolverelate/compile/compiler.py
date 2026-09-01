"""Deterministic RelOp → dialect SQL. No LLM. Shared algorithm with typescript/src/compiler.ts."""

from __future__ import annotations

from dataclasses import dataclass, field

from revolverelate.catalog import Engine, get_engine, placeholder, quote_ident


@dataclass
class _Ctx:
    engine: Engine
    params: list = field(default_factory=list)

    def q(self, name: str) -> str:
        return quote_ident(self.engine, name)

    def ph(self) -> str:
        self.params.append(None)  # replaced by emit_param
        return placeholder(self.engine, len(self.params))

    def emit_param(self, value) -> str:
        self.params.append(value)
        return placeholder(self.engine, len(self.params))


def compile_ir(doc: dict, engine: str | Engine) -> tuple[str, list]:
    eng = engine if isinstance(engine, Engine) else get_engine(engine)
    ctx = _Ctx(eng)
    kind = (doc or {}).get("kind") or "query"
    if kind == "query":
        sql = _compile_query(ctx, doc["op"])
    elif kind == "mutate":
        sql = _compile_mutate(ctx, doc["op"])
    elif kind == "txn":
        parts = ["BEGIN"]
        for stmt in doc.get("statements") or []:
            inner, params = compile_ir(stmt, eng)
            parts.append(inner)
            ctx.params.extend(params)
        parts.append("COMMIT")
        sql = "; ".join(parts)
    elif kind == "procedure":
        body = {"kind": "txn", "statements": doc.get("statements") or [{"kind": "mutate", "op": doc["op"]}]}
        return compile_ir(body, eng)
    else:
        raise ValueError(f"Unknown IR kind {kind!r}")
    return sql, list(ctx.params)


def _compile_query(ctx: _Ctx, op: dict) -> str:
    if op.get("op") == "setop":
        left = _compile_query(ctx, op["left"])
        right = _compile_query(ctx, op["right"])
        kind = (op.get("set") or "union").upper()
        if kind == "UNION" and not op.get("all") and ctx.engine.emit_family == "bigquery":
            return f"{left} UNION DISTINCT {right}"
        if op.get("all") and kind == "UNION":
            return f"{left} UNION ALL {right}"
        return f"{left} {kind} {right}"
    if op.get("op") == "with":
        ctes = []
        for cte in op.get("ctes") or []:
            inner = _compile_query(ctx, cte["input"])
            ctes.append(f"{ctx.q(cte['name'])} AS ({inner})")
        body = _compile_query(ctx, op["input"])
        return f"WITH {', '.join(ctes)} {body}"
    sel = _linearize(ctx, op)
    return _render_select(ctx, sel)


@dataclass
class _Select:
    distinct: bool = False
    items: list[str] | None = None
    frm: str = ""
    joins: list[str] = field(default_factory=list)
    where: str | None = None
    groups: list[str] = field(default_factory=list)
    having: str | None = None
    order: list[str] = field(default_factory=list)
    limit: int | None = None
    offset: int | None = None


def _linearize(ctx: _Ctx, op: dict) -> _Select:
    kind = op.get("op")
    if kind == "scan":
        alias = op.get("alias") or op["entity"]
        return _Select(frm=f"{ctx.q(op['entity'])} AS {ctx.q(alias)}")
    if kind == "values":
        cols = op.get("columns") or []
        alias = op.get("alias") or "v"
        raw_rows = op.get("rows") or []
        if cols:
            selects = []
            for row in raw_rows:
                parts = []
                for i, c in enumerate(cols):
                    val = row[i] if i < len(row) else None
                    parts.append(f"{ctx.emit_param(val)} AS {ctx.q(c)}")
                selects.append("SELECT " + ", ".join(parts))
            inner = " UNION ALL ".join(selects) if selects else "SELECT NULL"
            return _Select(frm=f"({inner}) AS {ctx.q(alias)}")
        rows = []
        for row in raw_rows:
            cells = ", ".join(ctx.emit_param(v) for v in row)
            rows.append(f"({cells})")
        values_sql = "VALUES " + ", ".join(rows)
        return _Select(frm=f"({values_sql}) AS {ctx.q(alias)}")
    if kind == "project":
        items = [_project_item(ctx, item) for item in op.get("items") or []]
        sel = _linearize(ctx, op["input"])
        if sel.groups:
            inner = _render_select(ctx, sel)
            sel = _Select(frm=f"({inner}) AS {ctx.q('q')}")
        sel.items = items
        return sel
    if kind == "filter":
        sel = _linearize(ctx, op["input"])
        pred = _compile_expr(ctx, op["predicate"])
        if sel.groups:
            sel.having = f"{sel.having} AND {pred}" if sel.having else pred
        else:
            items_sql = " ".join(sel.items or [])
            if " OVER " in items_sql:
                inner = _render_select(ctx, sel)
                sel = _Select(frm=f"({inner}) AS {ctx.q('q')}")
            sel.where = f"{sel.where} AND {pred}" if sel.where else pred
        return sel
    if kind == "having":
        sel = _linearize(ctx, op["input"])
        pred = _compile_expr(ctx, op["predicate"])
        sel.having = f"{sel.having} AND {pred}" if sel.having else pred
        return sel
    if kind == "join":
        left = _linearize(ctx, op["left"])
        right = _linearize(ctx, op["right"])
        jt = (op.get("joinType") or "inner").upper()
        if jt == "INNER":
            jt = "INNER JOIN"
        elif jt == "LEFT":
            jt = "LEFT JOIN"
        elif jt == "RIGHT":
            jt = "RIGHT JOIN"
        elif jt == "FULL":
            jt = "FULL OUTER JOIN"
        elif jt == "SEMI":
            jt = "INNER JOIN"
        elif jt == "ANTI":
            jt = "LEFT JOIN"
        else:
            jt = f"{jt} JOIN"
        on = " AND ".join(_compile_expr(ctx, p) for p in op.get("on") or [])
        right_from = right.frm
        left.joins.append(f"{jt} {right_from}" + (f" ON {on}" if on else ""))
        left.joins.extend(right.joins)
        if right.where:
            left.where = f"{left.where} AND {right.where}" if left.where else right.where
        return left
    if kind == "aggregate":
        groups = [_compile_expr(ctx, g) for g in op.get("groups") or []]
        aggs = [_project_item(ctx, a) for a in op.get("aggs") or []]
        sel = _linearize(ctx, op["input"])
        sel.groups = groups
        sel.items = groups + aggs
        return sel
    if kind == "sort":
        sel = _linearize(ctx, op["input"])
        keys = []
        for key in op.get("keys") or []:
            expr = _compile_expr(ctx, key["expr"])
            direction = (key.get("direction") or "ASC").upper()
            keys.append(f"{expr} {direction}")
        sel.order = keys
        return sel
    if kind == "limit":
        sel = _linearize(ctx, op["input"])
        sel.limit = int(op["count"])
        if op.get("offset"):
            sel.offset = int(op["offset"])
        return sel
    if kind == "distinct":
        sel = _linearize(ctx, op["input"])
        sel.distinct = True
        return sel
    if kind == "window":
        sel = _linearize(ctx, op["input"])
        extra = [_project_item(ctx, item) for item in op.get("items") or []]
        sel.items = (sel.items or ["*"]) + extra
        return sel
    # wrap unknown as subquery
    inner = _compile_query(ctx, op) if kind in {"setop", "with"} else _render_select(ctx, _Select())
    return _Select(frm=f"({inner}) AS {ctx.q('q')}")


def _project_item(ctx: _Ctx, item: dict) -> str:
    expr = item["expr"] if "expr" in item and isinstance(item.get("expr"), dict) else item
    sql = _compile_expr(ctx, expr)
    alias = item.get("alias")
    if alias:
        return f"{sql} AS {ctx.q(alias)}"
    return sql


def _compile_expr(ctx: _Ctx, expr: dict | None) -> str:
    if not expr:
        return "1"
    kind = expr.get("expr")
    if kind == "star":
        entity = expr.get("entity") or expr.get("alias")
        return f"{ctx.q(entity)}.*" if entity else "*"
    if kind == "col":
        entity = expr.get("entity") or expr.get("alias")
        attr = expr.get("attr")
        if entity and attr:
            return f"{ctx.q(entity)}.{ctx.q(attr)}"
        if attr:
            return ctx.q(attr)
        return ctx.q(entity or "col")
    if kind == "lit":
        return ctx.emit_param(expr.get("value"))
    if kind == "param":
        return ctx.emit_param(expr.get("value"))
    if kind == "agg":
        fn = (expr.get("fn") or "count").upper()
        inner = expr.get("input") or {"expr": "star"}
        if inner.get("expr") == "star" and fn == "COUNT" and not expr.get("distinct"):
            return f"{fn}(*)"
        if expr.get("distinct"):
            return f"{fn}(DISTINCT {_compile_expr(ctx, inner)})"
        return f"{fn}({_compile_expr(ctx, inner)})"
    if kind == "bin":
        op = expr.get("op") or "="
        if op.lower() == "and":
            return f"({_compile_expr(ctx, expr.get('left'))} AND {_compile_expr(ctx, expr.get('right'))})"
        if op.lower() == "or":
            return f"({_compile_expr(ctx, expr.get('left'))} OR {_compile_expr(ctx, expr.get('right'))})"
        if op.lower() == "in":
            values = expr.get("right", {}).get("value")
            if isinstance(values, (list, tuple)):
                ph = ", ".join(ctx.emit_param(v) for v in values)
                return f"{_compile_expr(ctx, expr['left'])} IN ({ph})"
        if op.lower() == "like":
            return f"{_compile_expr(ctx, expr['left'])} LIKE {_compile_expr(ctx, expr['right'])}"
        if op.lower() == "not like":
            return f"{_compile_expr(ctx, expr['left'])} NOT LIKE {_compile_expr(ctx, expr['right'])}"
        if op.lower() == "not in":
            values = expr.get("right", {}).get("value")
            if isinstance(values, (list, tuple)):
                ph = ", ".join(ctx.emit_param(v) for v in values)
                return f"{_compile_expr(ctx, expr['left'])} NOT IN ({ph})"
        if op.lower() == "between":
            values = expr.get("right", {}).get("value")
            if isinstance(values, (list, tuple)) and len(values) >= 2:
                return (
                    f"{_compile_expr(ctx, expr['left'])} BETWEEN {ctx.emit_param(values[0])} "
                    f"AND {ctx.emit_param(values[1])}"
                )
        if op.lower() == "is null":
            return f"{_compile_expr(ctx, expr.get('left'))} IS NULL"
        if op.lower() == "is not null":
            return f"{_compile_expr(ctx, expr.get('left'))} IS NOT NULL"
        return f"{_compile_expr(ctx, expr.get('left'))} {op} {_compile_expr(ctx, expr.get('right'))}"
    if kind == "over":
        fn = (expr.get("fn") or "rank").upper()
        inner = expr.get("input")
        call = f"{fn}()" if inner is None and fn in {"RANK", "DENSE_RANK", "ROW_NUMBER"} else f"{fn}({_compile_expr(ctx, inner) if inner else '*'})"
        parts = []
        parts_p = [_compile_expr(ctx, p) for p in expr.get("partition") or []]
        if parts_p:
            parts.append("PARTITION BY " + ", ".join(parts_p))
        parts_o = []
        for key in expr.get("order") or []:
            direction = (key.get("direction") or "ASC").upper()
            parts_o.append(f"{_compile_expr(ctx, key['expr'])} {direction}")
        if parts_o:
            parts.append("ORDER BY " + ", ".join(parts_o))
        return f"{call} OVER ({' '.join(parts)})"
    if kind == "un":
        op = (expr.get("op") or "not").upper()
        return f"{op} ({_compile_expr(ctx, expr.get('input'))})"
    if kind == "fn":
        fn = (expr.get("fn") or "FN").upper()
        args = ", ".join(_compile_expr(ctx, a) for a in expr.get("args") or [])
        return f"{fn}({args})"
    if kind == "case":
        parts = ["CASE"]
        for arm in expr.get("whens") or []:
            parts.append(f"WHEN {_compile_expr(ctx, arm.get('when'))} THEN {_compile_expr(ctx, arm.get('then'))}")
        if expr.get("else") is not None:
            parts.append(f"ELSE {_compile_expr(ctx, expr.get('else'))}")
        parts.append("END")
        return " ".join(parts)
    return "1"


def _render_select(ctx: _Ctx, sel: _Select) -> str:
    items = ", ".join(sel.items) if sel.items else "*"
    distinct = "DISTINCT " if sel.distinct else ""
    top = ""
    limit_sql = ""
    if sel.limit is not None:
        if ctx.engine.limit_style == "top":
            top = f"TOP {sel.limit} "
        elif ctx.engine.limit_style == "fetch":
            limit_sql = f" FETCH FIRST {sel.limit} ROWS ONLY"
        else:
            limit_sql = f" LIMIT {sel.limit}"
            if sel.offset:
                limit_sql += f" OFFSET {sel.offset}"
    sql = f"SELECT {distinct}{top}{items} FROM {sel.frm}"
    if sel.joins:
        sql += " " + " ".join(sel.joins)
    if sel.where:
        sql += f" WHERE {sel.where}"
    if sel.groups:
        sql += " GROUP BY " + ", ".join(sel.groups)
    if sel.having:
        sql += f" HAVING {sel.having}"
    if sel.order:
        sql += " ORDER BY " + ", ".join(sel.order)
    sql += limit_sql
    return sql


def _compile_mutate(ctx: _Ctx, op: dict) -> str:
    kind = op.get("op")
    if kind == "insert":
        cols = ", ".join(ctx.q(c) for c in op.get("columns") or [])
        if op.get("input"):
            select = _compile_query(ctx, op["input"])
            return f"INSERT INTO {ctx.q(op['entity'])} ({cols}) {select}"
        rows_sql = []
        for row in op.get("rows") or []:
            rows_sql.append("(" + ", ".join(ctx.emit_param(v) for v in row) + ")")
        return f"INSERT INTO {ctx.q(op['entity'])} ({cols}) VALUES {', '.join(rows_sql)}"
    if kind == "update":
        sets = []
        for col, expr in (op.get("set") or {}).items():
            if isinstance(expr, dict):
                sets.append(f"{ctx.q(col)} = {_compile_expr(ctx, expr)}")
            else:
                sets.append(f"{ctx.q(col)} = {ctx.emit_param(expr)}")
        sql = f"UPDATE {ctx.q(op['entity'])} SET {', '.join(sets)}"
        if op.get("predicate"):
            sql += f" WHERE {_compile_expr(ctx, op['predicate'])}"
        return sql
    if kind == "delete":
        sql = f"DELETE FROM {ctx.q(op['entity'])}"
        if op.get("predicate"):
            sql += f" WHERE {_compile_expr(ctx, op['predicate'])}"
        return sql
    if kind == "merge":
        target = ctx.q(op["target"])
        source = _compile_query(ctx, op["source"])
        on = _compile_expr(ctx, op["on"])
        if ctx.engine.emit_family in {"postgres", "sqlite", "duckdb"}:
            # compile as INSERT ... SELECT with ON CONFLICT when keys provided
            cols = op.get("columns") or []
            col_sql = ", ".join(ctx.q(c) for c in cols)
            return f"INSERT INTO {target} ({col_sql}) {source}"
        return f"MERGE INTO {target} USING ({source}) AS {ctx.q('src')} ON {on}"
    if kind == "call":
        args = ", ".join(ctx.emit_param(a) for a in op.get("args") or [])
        return f"CALL {ctx.q(op['procedure'])}({args})"
    raise ValueError(f"Unknown mutate op {kind!r}")
