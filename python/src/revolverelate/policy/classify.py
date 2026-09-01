"""Deterministic sensitivity tags. SLM may refine; this is the floor."""

from __future__ import annotations

_CRITICAL = (
    "ssn",
    "social_security",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "private_key",
    "credit_card",
    "card_number",
    "cvv",
    "iban",
)
_PII = (
    "email",
    "phone",
    "mobile",
    "address",
    "birth",
    "dob",
    "first_name",
    "last_name",
    "fullname",
    "full_name",
)


def classify_attribute(entity: str, name: str, type_name: str = "") -> str:
    key = name.casefold().replace(" ", "_")
    if any(token in key for token in _CRITICAL):
        return "critical"
    if any(token in key for token in _PII):
        return "pii"
    return "public"


def classify_graph(graph) -> dict[str, str]:
    tags: dict[str, str] = {}
    for entity in graph.all_entities():
        for attr in entity.attributes:
            tags[f"{entity.name}.{attr.name}"] = attr.sensitivity or classify_attribute(
                entity.name, attr.name, attr.type
            )
    return tags
