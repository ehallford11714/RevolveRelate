"""SLM jobs: fill RelOp, verify RelOp, propose policy. Never emit SQL."""

from __future__ import annotations

from revolverelate.errors import AskError
from revolverelate.ir.validate import validate_ir
from revolverelate.policy.accept import accept_policy, default_policy
from revolverelate.schema.model import SchemaGraph
from revolverelate.slm.complete import complete, extract_json
from revolverelate.slm.probe import probe_slm

_SYSTEM = (
    "You fill schema-bound relational algebra (RelOp). Never write SQL. "
    "Use only tables and columns from the schema. "
    "Ops: scan, project, filter, join, aggregate, sort, limit, distinct, setop, with. "
    "Expr: col, lit, bin, un, agg, star. "
    "JSON object with kind=query and op. No markdown."
)

_EXAMPLE = (
    '{"kind":"query","op":{"op":"limit","count":50,"input":{"op":"filter",'
    '"predicate":{"expr":"bin","op":"=","left":{"expr":"col","entity":"Customer","attr":"Region"},'
    '"right":{"expr":"lit","value":"West"}},"input":{"op":"scan","entity":"Customer","alias":"Customer"}}}}'
)


def schema_card(graph: SchemaGraph, policy: dict | None = None) -> str:
    lines = []
    hidden = set()
    if policy:
        for key, klass in (policy.get("attributes") or {}).items():
            if klass in {"critical", "pii"}:
                hidden.add(key.casefold())
    for entity in graph.all_entities():
        cols = []
        for attr in entity.attributes:
            key = f"{entity.name}.{attr.name}".casefold()
            if key in hidden:
                continue
            cols.append(f"{attr.name}:{attr.type}")
        lines.append(f"{entity.name}: " + ", ".join(cols))
    rels = [f"{r.from_entity}.{','.join(r.from_attrs)}->{r.to_entity}" for r in graph.relationships]
    return "TABLES\n" + "\n".join(lines) + "\nFKs\n" + "\n".join(rels)


def _entity_map(graph: SchemaGraph) -> dict[str, str]:
    return {e.name.casefold(): e.name for e in graph.all_entities()}


def _attr_map(graph: SchemaGraph) -> dict[tuple[str, str], str]:
    out = {}
    for entity in graph.all_entities():
        for attr in entity.attributes:
            out[(entity.name.casefold(), attr.name.casefold())] = attr.name
    return out


def _fix_name(value: str, names: dict[str, str]) -> str:
    return names.get(value.casefold(), value)


def _as_scan(name: str, entities: dict[str, str]) -> dict:
    resolved = _fix_name(name, entities)
    return {"op": "scan", "entity": resolved, "alias": resolved}


def normalize_relop(data: dict, graph: SchemaGraph) -> dict:
    """Coerce common SLM drift into schema-bound RelOp. Still never SQL."""
    if not isinstance(data, dict):
        raise AskError("SLM RelOp must be an object")
    if "kind" not in data and "op" in data:
        op = data["op"]
        data = {"kind": "query", "op": op if isinstance(op, dict) else data}
    if "kind" not in data and data.get("op") is None and isinstance(data.get("input"), dict):
        data = {"kind": "query", "op": data}
    data.setdefault("kind", "query")
    if isinstance(data.get("op"), str) and data["op"].casefold() in _entity_map(graph):
        data["op"] = _as_scan(data["op"], _entity_map(graph))
    entities = _entity_map(graph)
    attrs = _attr_map(graph)

    def walk(node):
        if isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, str) and item.casefold() in entities:
                    node[i] = _as_scan(item, entities)
                else:
                    walk(item)
            return
        if not isinstance(node, dict):
            return
        if isinstance(node.get("op"), str) and "entity" not in node and node["op"].casefold() in entities:
            node.update(_as_scan(node["op"], entities))
        for key in ("input", "left", "right"):
            child = node.get(key)
            if isinstance(child, str) and child.casefold() in entities:
                node[key] = _as_scan(child, entities)
            elif isinstance(child, str) and node.get("expr") == "bin":
                node[key] = {"expr": "lit", "value": child}
        if node.get("op") == "join":
            if not node.get("joinType") and node.get("type"):
                node["joinType"] = node["type"]
            on = node.get("on")
            if isinstance(on, dict):
                node["on"] = [on]
        for list_key in ("items", "groups", "aggs", "keys", "ctes", "statements"):
            val = node.get(list_key)
            if isinstance(val, dict):
                node[list_key] = [val]
        if node.get("entity"):
            node["entity"] = _fix_name(str(node["entity"]), entities)
        if node.get("alias") and str(node["alias"]).casefold() in entities:
            node["alias"] = entities[str(node["alias"]).casefold()]
        if node.get("expr") == "col" and node.get("entity") and node.get("attr"):
            key = (str(node["entity"]).casefold(), str(node["attr"]).casefold())
            if key in attrs:
                node["attr"] = attrs[key]
        for child in list(node.values()):
            walk(child)

    walk(data)
    return data


def _slm_relop(question: str, graph: SchemaGraph, policy: dict | None, slm, note: str = "") -> dict:
    prompt = (
        f"Question: {question}\nSchema:\n{schema_card(graph, policy)}\n"
        f"Return RelOp JSON only. Example:\n{_EXAMPLE}"
    )
    if note:
        prompt += f"\nPrevious attempt was invalid: {note}\nFix the RelOp. JSON only."
    text = complete(prompt, system=_SYSTEM, handle=slm, timeout=180.0)
    data = extract_json(text)
    return normalize_relop(data, graph)


def fill_relop(
    question: str,
    graph: SchemaGraph,
    policy: dict | None = None,
    *,
    fallback: bool = True,
) -> dict:
    slm = probe_slm()
    if not slm.available:
        from revolverelate.ir.nl import question_to_relop

        return question_to_relop(question, graph)
    last_error = ""
    for _attempt in range(2):
        try:
            data = _slm_relop(question, graph, policy, slm, last_error)
            if "kind" not in data:
                raise AskError("SLM RelOp missing kind")
            validate_ir(data, graph)
            return data
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
    if fallback:
        from revolverelate.ir.nl import question_to_relop

        return question_to_relop(question, graph)
    raise AskError(last_error or "SLM RelOp failed")


def verify_relop(ir: dict, graph: SchemaGraph) -> list[str]:
    return validate_ir(ir, graph)


def synthesize_policy(graph: SchemaGraph) -> dict:
    base = default_policy(graph)
    slm = probe_slm()
    if not slm.available:
        return accept_policy(base, graph)
    try:
        text = complete(
            f"Propose policy JSON for:\n{schema_card(graph)}",
            system="Return policy version 1 with attributes and capabilities. Do not grant mutate_live.",
            handle=slm,
        )
        proposed = extract_json(text)
        proposed.setdefault("version", 1)
        proposed.setdefault("attributes", base["attributes"])
        return accept_policy(proposed, graph)
    except Exception:
        return accept_policy(base, graph)


_CAUSAL_SYSTEM = (
    "You fill a CausalPlan. Never write SQL. Never write Chroma where-filters. Never write CASE. "
    "Return JSON: kind=causal_plan, query, goal={measure,dimension,slice}, steps=[{op, ...binds}]. "
    "Use only allowed primitive ids. RelOp compiler will write SQL."
)


def fill_causal_plan(question: str, graph: SchemaGraph | None = None, policy: dict | None = None) -> dict:
    """SLM proposes a primitive chain. Fallback composites when SLM is off or illegal."""
    from revolverelate.analytics.causal_plan import allowed_ops, fallback_causal_plan, normalize_causal_plan

    slm = probe_slm()
    if not slm.available:
        return fallback_causal_plan(question, graph)
    ops = ", ".join(sorted(allowed_ops()))
    prompt = (
        f"Question: {question}\nAllowed ops: {ops}\n"
        "Example: {\"kind\":\"causal_plan\",\"query\":\"sales fell because discounting\","
        "\"steps\":[{\"op\":\"overlay\",\"column\":\"ProductName\"},"
        "{\"op\":\"chunk_causal\",\"column\":\"ProductName\"},"
        "{\"op\":\"knn\",\"query\":\"sales fell because discounting\",\"n\":8,\"column\":\"ProductName\"},"
        "{\"op\":\"pair_causal\"},{\"op\":\"hypothesize\",\"name\":\"Pairs\"}]}"
    )
    if graph is not None:
        prompt = f"{prompt}\nSchema:\n{schema_card(graph, policy)}"
    try:
        text = complete(prompt, system=_CAUSAL_SYSTEM, handle=slm, timeout=180.0)
        data = extract_json(text)
        if not isinstance(data, dict):
            return fallback_causal_plan(question, graph)
        return normalize_causal_plan(data, question, graph)
    except Exception:
        return fallback_causal_plan(question, graph)
