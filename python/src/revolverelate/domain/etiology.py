"""Possible etiology evidence from live RelOp pairs. Identification is none — not proof."""

from __future__ import annotations

from revolverelate.domain.mine import catalog_targets, extract_targets
from revolverelate.domain.reflect import _clip


def collect_etiologies(
    causal: dict | None,
    *,
    proposed: list[str] | None = None,
    added: list[str] | None = None,
    pass_no: int = 1,
) -> list[dict]:
    """One evidence row per catalog gene mentioned in a live cause/effect pair."""
    live = (causal or {}).get("live") if isinstance(causal, dict) else {}
    rows = list((live or {}).get("rows") or [])
    cols = [str(c) for c in (live or {}).get("columns") or []]
    idx = {c.casefold(): i for i, c in enumerate(cols)}

    def cell(row, name, fallback=""):
        pos = idx.get(name)
        if pos is None or pos >= len(row):
            return fallback
        return str(row[pos] or "")

    catalog = catalog_targets()
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
        for rec in hits:
            symbol = str(rec.get("symbol") or "")
            key = f"{symbol.casefold()}|{cue.casefold()}|{pk}"
            if not symbol or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "candidate": symbol,
                    "kind": "possible_etiology",
                    "identification": "none",
                    "evidenceGrade": "heuristic",
                    "conclusive": False,
                    "cue": _clip(cue, 24),
                    "cause": _clip(cause),
                    "effect": _clip(effect),
                    "source": {"entity": entity, "column": column, "pk": pk},
                    "pass": pass_no,
                    "note": "Bound overlay discourse + catalog gene. Possible etiology evidence, not conclusive proof.",
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
                "kind": "possible_etiology",
                "identification": "none",
                "evidenceGrade": "heuristic",
                "conclusive": False,
                "cue": "catalog",
                "cause": "",
                "effect": "",
                "source": {"entity": "Gene", "column": "Symbol", "pk": symbol},
                "pass": pass_no,
                "note": "Catalog follow-on proposed from live text. Possible etiology evidence, not conclusive proof.",
            }
        )
    return out


def merge_etiologies(*batches: list[dict]) -> list[dict]:
    """Dedupe by candidate+cue, keep first (earlier pass) evidence."""
    seen: set[str] = set()
    out: list[dict] = []
    for batch in batches:
        for row in batch or []:
            key = f"{str(row.get('candidate') or '').casefold()}|{str(row.get('cue') or '').casefold()}"
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
