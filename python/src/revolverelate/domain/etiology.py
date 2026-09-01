"""Possible-cause evidence from live RelOp pairs. Identification is none — not proof.

Domain-neutral: the catalog, evidence kind (possible_etiology / possible_driver),
and symbol entity come from the detected domain.
"""

from __future__ import annotations

from revolverelate.domain.mine import catalog_targets, extract_targets
from revolverelate.domain.reflect import _clip


def collect_etiologies(
    causal: dict | None,
    *,
    proposed: list[str] | None = None,
    added: list[str] | None = None,
    pass_no: int = 1,
    catalog: dict | None = None,
    kind: str = "possible_etiology",
    label: str = "possible etiology",
    symbol_entity: str = "Gene",
    symbol_column: str = "Symbol",
    gate: str | None = None,
    driver_terms: dict[str, str] | None = None,
) -> list[dict]:
    """One evidence row per catalogued candidate mentioned in a live cause/effect pair."""
    live = (causal or {}).get("live") if isinstance(causal, dict) else {}
    rows = list((live or {}).get("rows") or [])
    cols = [str(c) for c in (live or {}).get("columns") or []]
    idx = {c.casefold(): i for i, c in enumerate(cols)}

    def cell(row, name, fallback=""):
        pos = idx.get(name)
        if pos is None or pos >= len(row):
            return fallback
        return str(row[pos] or "")

    catalog = catalog if catalog is not None else catalog_targets()
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        cause = cell(row, "causetext", row[0] if row else "")
        effect = cell(row, "effecttext", row[1] if len(row) > 1 else "")
        cue = cell(row, "cue")
        entity = cell(row, "entity")
        column = cell(row, "column")
        pk = cell(row, "sourcepk")
        blob = f"{cause} {effect} {cue}"
        hits = extract_targets(blob, known=set(), catalog=catalog)
        if not hits:
            continue
        driver = _driver(blob, driver_terms)
        for rec in hits:
            symbol = str(rec.get("symbol") or "")
            key = f"{symbol.casefold()}|{cue.casefold()}|{driver}|{pk}"
            if not symbol or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "candidate": symbol,
                    "kind": kind,
                    "identification": "none",
                    "evidenceGrade": "heuristic",
                    "conclusive": False,
                    "cue": _clip(cue, 24),
                    "driver": driver,
                    "cause": _clip(cause),
                    "effect": _clip(effect),
                    "source": {"entity": entity, "column": column, "pk": pk},
                    "pass": pass_no,
                    "gate": gate,
                    "note": f"Bound overlay discourse + catalogued candidate. {label.capitalize()} evidence, not conclusive proof.",
                }
            )
    extra_blob = " ".join(list(proposed or []) + list(added or []))
    for rec in extract_targets(extra_blob, known={e["candidate"].casefold() for e in out}, catalog=catalog):
        symbol = str(rec.get("symbol") or "")
        if not symbol or symbol.casefold() in seen:
            continue
        seen.add(symbol.casefold())
        out.append(
            {
                "candidate": symbol,
                "kind": kind,
                "identification": "none",
                "evidenceGrade": "heuristic",
                "conclusive": False,
                "cue": "catalog",
                "cause": "",
                "effect": "",
                "source": {"entity": symbol_entity, "column": symbol_column, "pk": symbol},
                "pass": pass_no,
                "gate": gate,
                "note": f"Catalog follow-on proposed from live text. {label.capitalize()} evidence, not conclusive proof.",
            }
        )
    return out


def _driver(blob: str, terms: dict[str, str] | None) -> str:
    """Label the measured driver named in a pair (e.g. volume / gap / regime / earnings). Domain-supplied terms."""
    if not terms:
        return ""
    low = (blob or "").casefold()
    for needle, label in terms.items():
        if str(needle).casefold() in low:
            return str(label)
    return ""


def merge_etiologies(*batches: list[dict]) -> list[dict]:
    """Dedupe by candidate+cue(+driver), keep first (earlier pass) evidence."""
    seen: set[str] = set()
    out: list[dict] = []
    for batch in batches:
        for row in batch or []:
            key = f"{str(row.get('candidate') or '').casefold()}|{str(row.get('cue') or '').casefold()}|{str(row.get('driver') or '').casefold()}"
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def etiology_candidates(rows: list[dict]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        name = str(row.get("candidate") or "")
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            found.append(name)
    return found
