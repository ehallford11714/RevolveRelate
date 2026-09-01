/** Deterministic RelOp → dialect SQL. Shared algorithm with python compile/compiler.py. */

import { type Engine, getEngine, placeholder, quoteIdent } from "./catalog.ts";

type Expr = Record<string, any>;
type Op = Record<string, any>;
type Doc = { kind?: string; op?: Op; statements?: Doc[]; name?: string };

type Ctx = { engine: Engine; params: unknown[] };

function q(ctx: Ctx, name: string): string {
  return quoteIdent(ctx.engine, name);
}

function emitParam(ctx: Ctx, value: unknown): string {
  ctx.params.push(value);
  return placeholder(ctx.engine, ctx.params.length);
}

export function compileIr(doc: Doc, engine: string | Engine): { sql: string; params: unknown[] } {
  const eng = typeof engine === "string" ? getEngine(engine) : engine;
  const ctx: Ctx = { engine: eng, params: [] };
  const kind = doc.kind || "query";
  let sql = "";
  if (kind === "query") {
    sql = compileQuery(ctx, doc.op!);
  } else if (kind === "mutate") {
    sql = compileMutate(ctx, doc.op!);
  } else if (kind === "txn") {
    const parts = ["BEGIN"];
    for (const stmt of doc.statements || []) {
      const inner = compileIr(stmt, eng);
      parts.push(inner.sql);
      ctx.params.push(...inner.params);
    }
    parts.push("COMMIT");
    sql = parts.join("; ");
  } else if (kind === "procedure") {
    return compileIr(
      { kind: "txn", statements: doc.statements || [{ kind: "mutate", op: doc.op }] },
      eng,
    );
  } else {
    throw new Error(`Unknown IR kind ${kind}`);
  }
  return { sql, params: ctx.params };
}

function compileQuery(ctx: Ctx, op: Op): string {
  if (op.op === "setop") {
    const left = compileQuery(ctx, op.left);
    const right = compileQuery(ctx, op.right);
    const kind = String(op.set || "union").toUpperCase();
    if (kind === "UNION" && !op.all && ctx.engine.emitFamily === "bigquery") {
      return `${left} UNION DISTINCT ${right}`;
    }
    if (op.all && kind === "UNION") return `${left} UNION ALL ${right}`;
    return `${left} ${kind} ${right}`;
  }
  if (op.op === "with") {
    const ctes = (op.ctes || []).map(
      (cte: Op) => `${q(ctx, cte.name)} AS (${compileQuery(ctx, cte.input)})`,
    );
    return `WITH ${ctes.join(", ")} ${compileQuery(ctx, op.input)}`;
  }
  return renderSelect(ctx, linearize(ctx, op));
}

type Select = {
  distinct: boolean;
  items: string[] | null;
  frm: string;
  joins: string[];
  where: string | null;
  groups: string[];
  having: string | null;
  order: string[];
  limit: number | null;
  offset: number | null;
};

function emptySelect(): Select {
  return {
    distinct: false,
    items: null,
    frm: "",
    joins: [],
    where: null,
    groups: [],
    having: null,
    order: [],
    limit: null,
    offset: null,
  };
}

function linearize(ctx: Ctx, op: Op): Select {
  const kind = op.op;
  if (kind === "scan") {
    const alias = op.alias || op.entity;
    const sel = emptySelect();
    sel.frm = `${q(ctx, op.entity)} AS ${q(ctx, alias)}`;
    return sel;
  }
  if (kind === "values") {
    const alias = op.alias || "v";
    const cols = op.columns || [];
    const rawRows: unknown[][] = op.rows || [];
    const sel = emptySelect();
    if (cols.length) {
      const selects = rawRows.map((row) => {
        const parts = cols.map((c: string, i: number) => `${emitParam(ctx, row[i])} AS ${q(ctx, c)}`);
        return `SELECT ${parts.join(", ")}`;
      });
      const inner = selects.length ? selects.join(" UNION ALL ") : "SELECT NULL";
      sel.frm = `(${inner}) AS ${q(ctx, alias)}`;
      return sel;
    }
    const rows = rawRows.map(
      (row: unknown[]) => `(${row.map((v) => emitParam(ctx, v)).join(", ")})`,
    );
    sel.frm = `(VALUES ${rows.join(", ")}) AS ${q(ctx, alias)}`;
    return sel;
  }
  if (kind === "project") {
    const items = (op.items || []).map((item: Op) => projectItem(ctx, item));
    const sel = linearize(ctx, op.input);
    sel.items = items;
    return sel;
  }
  if (kind === "filter") {
    const sel = linearize(ctx, op.input);
    const pred = compileExpr(ctx, op.predicate);
    if (sel.groups.length) sel.having = sel.having ? `${sel.having} AND ${pred}` : pred;
    else {
      const itemsSql = (sel.items || []).join(" ");
      if (itemsSql.includes(" OVER ")) {
        const inner = renderSelect(ctx, sel);
        const wrapped = emptySelect();
        wrapped.frm = `(${inner}) AS ${q(ctx, "q")}`;
        wrapped.where = pred;
        return wrapped;
      }
      sel.where = sel.where ? `${sel.where} AND ${pred}` : pred;
    }
    return sel;
  }
  if (kind === "having") {
    const sel = linearize(ctx, op.input);
    const pred = compileExpr(ctx, op.predicate);
    sel.having = sel.having ? `${sel.having} AND ${pred}` : pred;
    return sel;
  }
  if (kind === "join") {
    const left = linearize(ctx, op.left);
    const right = linearize(ctx, op.right);
    let jt = String(op.joinType || "inner").toUpperCase();
    if (jt === "INNER") jt = "INNER JOIN";
    else if (jt === "LEFT") jt = "LEFT JOIN";
    else if (jt === "RIGHT") jt = "RIGHT JOIN";
    else if (jt === "FULL") jt = "FULL OUTER JOIN";
    else if (jt === "SEMI") jt = "INNER JOIN";
    else if (jt === "ANTI") jt = "LEFT JOIN";
    else jt = `${jt} JOIN`;
    const on = (op.on || []).map((p: Expr) => compileExpr(ctx, p)).join(" AND ");
    left.joins.push(`${jt} ${right.frm}` + (on ? ` ON ${on}` : ""));
    left.joins.push(...right.joins);
    if (right.where) left.where = left.where ? `${left.where} AND ${right.where}` : right.where;
    return left;
  }
  if (kind === "aggregate") {
    const groups = (op.groups || []).map((g: Expr) => compileExpr(ctx, g));
    const aggs = (op.aggs || []).map((a: Op) => projectItem(ctx, a));
    const sel = linearize(ctx, op.input);
    sel.groups = groups;
    sel.items = groups.concat(aggs);
    return sel;
  }
  if (kind === "sort") {
    const sel = linearize(ctx, op.input);
    sel.order = (op.keys || []).map((key: Op) => {
      const expr = compileExpr(ctx, key.expr);
      const direction = String(key.direction || "ASC").toUpperCase();
      return `${expr} ${direction}`;
    });
    return sel;
  }
  if (kind === "limit") {
    const sel = linearize(ctx, op.input);
    sel.limit = Number(op.count);
    if (op.offset) sel.offset = Number(op.offset);
    return sel;
  }
  if (kind === "distinct") {
    const sel = linearize(ctx, op.input);
    sel.distinct = true;
    return sel;
  }
  if (kind === "window") {
    const sel = linearize(ctx, op.input);
    const extra = (op.items || []).map((item: Op) => projectItem(ctx, item));
    sel.items = (sel.items || ["*"]).concat(extra);
    return sel;
  }
  const inner = compileQuery(ctx, op);
  const sel = emptySelect();
  sel.frm = `(${inner}) AS ${q(ctx, "q")}`;
  return sel;
}

function projectItem(ctx: Ctx, item: Op): string {
  const expr = item.expr && typeof item.expr === "object" ? item.expr : item;
  const sql = compileExpr(ctx, expr);
  if (item.alias) return `${sql} AS ${q(ctx, item.alias)}`;
  return sql;
}

function compileExpr(ctx: Ctx, expr: Expr | undefined): string {
  if (!expr) return "1";
  const kind = expr.expr;
  if (kind === "star") {
    const entity = expr.entity || expr.alias;
    return entity ? `${q(ctx, entity)}.*` : "*";
  }
  if (kind === "col") {
    const entity = expr.entity || expr.alias;
    const attr = expr.attr;
    if (entity && attr) return `${q(ctx, entity)}.${q(ctx, attr)}`;
    if (attr) return q(ctx, attr);
    return q(ctx, entity || "col");
  }
  if (kind === "lit" || kind === "param") return emitParam(ctx, expr.value);
  if (kind === "agg") {
    const fn = String(expr.fn || "count").toUpperCase();
    const inner = expr.input || { expr: "star" };
    if (inner.expr === "star" && fn === "COUNT") return `${fn}(*)`;
    return `${fn}(${compileExpr(ctx, inner)})`;
  }
  if (kind === "bin") {
    const op = expr.op || "=";
    if (String(op).toLowerCase() === "and") {
      return `(${compileExpr(ctx, expr.left)} AND ${compileExpr(ctx, expr.right)})`;
    }
    if (String(op).toLowerCase() === "or") {
      return `(${compileExpr(ctx, expr.left)} OR ${compileExpr(ctx, expr.right)})`;
    }
    if (String(op).toLowerCase() === "in") {
      const values = expr.right?.value;
      if (Array.isArray(values)) {
        const ph = values.map((v: unknown) => emitParam(ctx, v)).join(", ");
        return `${compileExpr(ctx, expr.left)} IN (${ph})`;
      }
    }
    if (String(op).toLowerCase() === "like") {
      return `${compileExpr(ctx, expr.left)} LIKE ${compileExpr(ctx, expr.right)}`;
    }
    return `${compileExpr(ctx, expr.left)} ${op} ${compileExpr(ctx, expr.right)}`;
  }
  if (kind === "over") {
    const fn = String(expr.fn || "rank").toUpperCase();
    const inner = expr.input;
    const call =
      inner == null && ["RANK", "DENSE_RANK", "ROW_NUMBER"].includes(fn)
        ? `${fn}()`
        : `${fn}(${inner ? compileExpr(ctx, inner) : "*"})`;
    const parts: string[] = [];
    const partsP = (expr.partition || []).map((p: Expr) => compileExpr(ctx, p));
    if (partsP.length) parts.push("PARTITION BY " + partsP.join(", "));
    const partsO = (expr.order || []).map((key: Op) => {
      const direction = String(key.direction || "ASC").toUpperCase();
      return `${compileExpr(ctx, key.expr)} ${direction}`;
    });
    if (partsO.length) parts.push("ORDER BY " + partsO.join(", "));
    return `${call} OVER (${parts.join(" ")})`;
  }
  if (kind === "un") {
    const op = String(expr.op || "not").toUpperCase();
    return `${op} (${compileExpr(ctx, expr.input)})`;
  }
  if (kind === "fn") {
    const fn = String(expr.fn || "FN").toUpperCase();
    const args = (expr.args || []).map((a: Expr) => compileExpr(ctx, a)).join(", ");
    return `${fn}(${args})`;
  }
  if (kind === "case") {
    const parts = ["CASE"];
    for (const arm of expr.whens || []) {
      parts.push(`WHEN ${compileExpr(ctx, arm.when)} THEN ${compileExpr(ctx, arm.then)}`);
    }
    if (expr.else != null) parts.push(`ELSE ${compileExpr(ctx, expr.else)}`);
    parts.push("END");
    return parts.join(" ");
  }
  return "1";
}

function renderSelect(ctx: Ctx, sel: Select): string {
  const items = sel.items ? sel.items.join(", ") : "*";
  const distinct = sel.distinct ? "DISTINCT " : "";
  let top = "";
  let limitSql = "";
  if (sel.limit !== null) {
    if (ctx.engine.limitStyle === "top") top = `TOP ${sel.limit} `;
    else if (ctx.engine.limitStyle === "fetch") limitSql = ` FETCH FIRST ${sel.limit} ROWS ONLY`;
    else {
      limitSql = ` LIMIT ${sel.limit}`;
      if (sel.offset) limitSql += ` OFFSET ${sel.offset}`;
    }
  }
  let sql = `SELECT ${distinct}${top}${items} FROM ${sel.frm}`;
  if (sel.joins.length) sql += " " + sel.joins.join(" ");
  if (sel.where) sql += ` WHERE ${sel.where}`;
  if (sel.groups.length) sql += " GROUP BY " + sel.groups.join(", ");
  if (sel.having) sql += ` HAVING ${sel.having}`;
  if (sel.order.length) sql += " ORDER BY " + sel.order.join(", ");
  return sql + limitSql;
}

function compileMutate(ctx: Ctx, op: Op): string {
  const kind = op.op;
  if (kind === "insert") {
    const cols = (op.columns || []).map((c: string) => q(ctx, c)).join(", ");
    if (op.input) return `INSERT INTO ${q(ctx, op.entity)} (${cols}) ${compileQuery(ctx, op.input)}`;
    const rows = (op.rows || []).map(
      (row: unknown[]) => `(${row.map((v) => emitParam(ctx, v)).join(", ")})`,
    );
    return `INSERT INTO ${q(ctx, op.entity)} (${cols}) VALUES ${rows.join(", ")}`;
  }
  if (kind === "update") {
    const sets = Object.entries(op.set || {}).map(([col, expr]) => {
      if (expr && typeof expr === "object" && (expr as Expr).expr) {
        return `${q(ctx, col)} = ${compileExpr(ctx, expr as Expr)}`;
      }
      return `${q(ctx, col)} = ${emitParam(ctx, expr)}`;
    });
    let sql = `UPDATE ${q(ctx, op.entity)} SET ${sets.join(", ")}`;
    if (op.predicate) sql += ` WHERE ${compileExpr(ctx, op.predicate)}`;
    return sql;
  }
  if (kind === "delete") {
    let sql = `DELETE FROM ${q(ctx, op.entity)}`;
    if (op.predicate) sql += ` WHERE ${compileExpr(ctx, op.predicate)}`;
    return sql;
  }
  if (kind === "merge") {
    const target = q(ctx, op.target);
    const source = compileQuery(ctx, op.source);
    const on = compileExpr(ctx, op.on);
    if (["postgres", "sqlite", "duckdb"].includes(ctx.engine.emitFamily)) {
      const cols = (op.columns || []).map((c: string) => q(ctx, c)).join(", ");
      return `INSERT INTO ${target} (${cols}) ${source}`;
    }
    return `MERGE INTO ${target} USING (${source}) AS ${q(ctx, "src")} ON ${on}`;
  }
  if (kind === "call") {
    const args = (op.args || []).map((a: unknown) => emitParam(ctx, a)).join(", ");
    return `CALL ${q(ctx, op.procedure)}(${args})`;
  }
  throw new Error(`Unknown mutate op ${kind}`);
}
