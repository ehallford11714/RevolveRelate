"""Extract next catalog mine targets from RelOp reflection text. Never SQL from an SLM."""

from __future__ import annotations

import json
import re

from revolverelate.catalog import spec_dir

_TOKEN = re.compile(r"\b([A-Z][A-Z0-9]{1,9}|NP_\d+\.\d+)\b")
_SKIP = {"THE", "AND", "FOR", "REAL", "TEXT", "FROM", "WITH", "AS", "ON", "OR", "NOT", "NULL"}


def load_automine_spec() -> dict:
    return json.loads((spec_dir() / "automine.json").read_text(encoding="utf-8"))


def domain_catalog(domain: str = "gene") -> dict:
    path = spec_dir() / f"domain-{domain}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def catalog_targets(spec: dict | None = None) -> dict[str, dict]:
    spec = spec if isinstance(spec, dict) else domain_catalog()
    out: dict[str, dict] = {}
    for row in list(spec.get("accessions") or []) + list(spec.get("universe") or []) + list(spec.get("followOn") or []):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            out[symbol.casefold()] = row
        protein = str(row.get("protein") or "").strip()
        if protein:
            out[protein.casefold()] = row
    return out


def flatten_cells(rows) -> str:
    parts: list[str] = []
    for row in rows or []:
        if isinstance(row, dict):
            parts.extend(str(v) for v in row.values() if v is not None)
        else:
            parts.extend(str(v) for v in row if v is not None)
    return " ".join(parts)


def extract_targets(text: str, *, known: set[str] | None = None, catalog: dict | None = None) -> list[dict]:
    """Return catalogued symbols mentioned in text that are not already mined."""
    catalog = catalog if catalog is not None else catalog_targets()
    have = {s.casefold() for s in (known or set())}
    hits: list[dict] = []
    seen: set[str] = set()
    blob = text or ""
    for match in _TOKEN.findall(blob):
        key = match.casefold()
        if key in _SKIP or key in have or key in seen:
            continue
        row = catalog.get(key)
        if not row:
            continue
        symbol = str(row.get("symbol") or match)
        if symbol.casefold() in have:
            continue
        seen.add(key)
        seen.add(symbol.casefold())
        hits.append(row)
    return hits
