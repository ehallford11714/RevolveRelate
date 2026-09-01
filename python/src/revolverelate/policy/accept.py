"""Deterministic Policy acceptor. SLM proposals are rejected unless they pass."""

from __future__ import annotations

from revolverelate.errors import PolicyError
from revolverelate.policy.classify import classify_graph

_CLASSES = {"public", "internal", "critical", "pii"}
_CAPS = {
    "read_sandbox",
    "read_live",
    "mutate_sandbox",
    "mutate_live",
    "call_procedure",
    "reveal",
}


def default_policy(graph) -> dict:
    return {
        "version": 1,
        "attributes": classify_graph(graph),
        "capabilities": ["read_sandbox", "mutate_sandbox"],
        "entityAllowlist": [e.name for e in graph.all_entities()],
        "rowPredicates": {},
        "procedures": [],
        "reveal": [],
        "notes": ["deterministic default; live mutate is off until an operator grants it"],
    }


def accept_policy(proposed: dict, graph) -> dict:
    if not isinstance(proposed, dict):
        raise PolicyError("Policy must be an object")
    if proposed.get("version") != 1:
        raise PolicyError("Policy version must be 1")
    attrs = proposed.get("attributes") or {}
    if not isinstance(attrs, dict):
        raise PolicyError("attributes must be a map")
    known = {
        f"{e.name}.{a.name}".casefold(): f"{e.name}.{a.name}"
        for e in graph.all_entities()
        for a in e.attributes
    }
    cleaned_attrs = {}
    for key, klass in attrs.items():
        if klass not in _CLASSES:
            raise PolicyError(f"Unknown attribute class {klass!r}")
        if key.casefold() not in known:
            raise PolicyError(f"Policy names unknown attribute {key!r}")
        cleaned_attrs[known[key.casefold()]] = klass
    floor = classify_graph(graph)
    for key, klass in floor.items():
        if klass in {"critical", "pii"} and cleaned_attrs.get(key, klass) == "public":
            raise PolicyError(f"Cannot downgrade {key} from {klass} to public")
        cleaned_attrs.setdefault(key, klass)
    caps = list(proposed.get("capabilities") or [])
    for cap in caps:
        if cap not in _CAPS:
            raise PolicyError(f"Unknown capability {cap!r}")
    if "mutate_live" in caps and "read_sandbox" not in caps:
        raise PolicyError("mutate_live requires read_sandbox so the dummy dup is validated first")
    allow = list(proposed.get("entityAllowlist") or [e.name for e in graph.all_entities()])
    for name in allow:
        if graph.entity(name) is None:
            raise PolicyError(f"Allowlist names unknown entity {name!r}")
    return {
        "version": 1,
        "attributes": cleaned_attrs,
        "capabilities": caps or ["read_sandbox", "mutate_sandbox"],
        "entityAllowlist": allow,
        "rowPredicates": dict(proposed.get("rowPredicates") or {}),
        "procedures": list(proposed.get("procedures") or []),
        "reveal": list(proposed.get("reveal") or []),
        "notes": list(proposed.get("notes") or []),
    }
