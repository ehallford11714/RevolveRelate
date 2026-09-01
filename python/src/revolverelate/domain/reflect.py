"""Pivot + splice for the automine reflect agent. Re-cause each pass until a goal lands.

Domain-neutral: pivot columns, families, and the catalog come from the detected
spec/domain-*.json (see revolverelate.domain.registry).
"""

from __future__ import annotations

import re

from revolverelate.analytics.bind import list_dimensions
from revolverelate.domain.mine import catalog_targets, extract_targets, flatten_cells

_PIVOT_COLUMNS = ("Abstract", "Evidence", "Summary", "Header")
_FAMILIES = ("gene", "epitope", "sirna")


def _clip(text: str, n: int = 96) -> str:
    body = re.sub(r"\s+", " ", (text or "").strip())
    return body[:n].rstrip()


def splice_details(
    causal: dict | None,
    *,
    proposed: list[str] | None = None,
    added: list[str] | None = None,
    catalog: dict | None = None,
) -> dict:
    """Pull cause/effect/cue/symbols from a live causal RelOp so the next ask can splice them."""
    live = (causal or {}).get("live") if isinstance(causal, dict) else {}
    rows = (live or {}).get("rows") or []
    cols = [str(c) for c in (live or {}).get("columns") or []]
    idx = {c.casefold(): i for i, c in enumerate(cols)}
    cause = effect = cue = ""
    if rows:
        row = rows[0]
        cause = str(row[idx["causetext"]] if "causetext" in idx and idx["causetext"] < len(row) else row[0] if row else "")
        effect = str(row[idx["effecttext"]] if "effecttext" in idx and idx["effecttext"] < len(row) else row[1] if len(row) > 1 else "")
        cue = str(row[idx["cue"]] if "cue" in idx and idx["cue"] < len(row) else "")
    blob = " ".join([flatten_cells(rows), " ".join(proposed or []), " ".join(added or [])])
    symbols = []
    seen: set[str] = set()
    for rec in extract_targets(blob, known=set(), catalog=catalog if catalog is not None else catalog_targets()):
        symbol = str(rec.get("symbol") or "")
        if symbol and symbol.casefold() not in seen:
            seen.add(symbol.casefold())
            symbols.append(symbol)
    for extra in list(proposed or []) + list(added or []):
        if extra and extra.casefold() not in seen:
            seen.add(extra.casefold())
            symbols.append(str(extra))
    return {
        "cause": _clip(cause),
        "effect": _clip(effect),
        "cue": _clip(cue, 24),
        "symbols": symbols[:6],
        "livePairs": int((live or {}).get("rowCount") or 0),
    }


def splice_question(seed: str, details: dict, *, column: str | None = None, family: str | None = None) -> str:
    """Keep the causal ask, splice symbols/cues, optionally pivot column or family."""
    ask = (seed or "what causes this").strip()
    if "cause" not in ask.casefold():
        ask = f"what causes {ask}"
    symbols = [s for s in (details.get("symbols") or []) if s]
    if symbols:
        via = " ".join(symbols[:3])
        if via.casefold() not in ask.casefold():
            ask = f"{ask} via {via}"
    cue = str(details.get("cue") or "").strip()
    cause = str(details.get("cause") or "").strip()
    if cue and cause and cause.casefold() not in ask.casefold():
        ask = f"{ask} {cue} {cause}"
    if column and column.casefold() not in ask.casefold():
        ask = f"{ask} {column}"
    if family and family.casefold() not in ask.casefold():
        ask = f"{ask} {family}"
    return _clip(ask, 400)


def next_pivot_column(graph, asked: str, *, live_pairs: int, columns: tuple[str, ...] | list[str] | None = None) -> str | None:
    """When live pairs are empty, rotate through the domain's text columns that exist on this schema."""
    dims = {d.casefold(): d for d in list_dimensions(graph)}
    cycle = [dims[c.casefold()] for c in (columns or _PIVOT_COLUMNS) if c.casefold() in dims]
    if not cycle:
        return None
    words = set(re.findall(r"[a-z0-9]+", (asked or "").casefold()))
    current = next((c for c in cycle if c.casefold() in words), cycle[0])
    if live_pairs > 0:
        return current
    i = cycle.index(current)
    return cycle[(i + 1) % len(cycle)]


def next_family(pass_no: int, families: tuple[str, ...] | list[str] | None = None) -> str:
    """Rotate the domain's families so later passes cover each catalogue."""
    fams = tuple(families or _FAMILIES)
    return fams[max(int(pass_no) - 1, 0) % len(fams)]


def goal_reached(spec: dict, *, details: dict, spliced: bool, mined: list[str], etiologies: list | None = None) -> dict:
    """Declared evidence goal: enough possible candidates after a spliced re-cause. Not proof."""
    goal = spec.get("goal") if isinstance(spec.get("goal"), dict) else {}
    min_pairs = int(goal.get("minLivePairs") or 1)
    min_tokens = int(goal.get("minSplicedTokens") or 1)
    min_et = int(goal.get("minEtiologies") or 1)
    need_splice = bool(goal.get("requireRespliceCausal", True))
    need_mine = bool(goal.get("mineFollowOn", False))
    pairs = int(details.get("livePairs") or 0)
    tokens = len(details.get("symbols") or [])
    et_n = len({str(e.get("candidate") or "").casefold() for e in (etiologies or []) if e.get("candidate")})
    ok = pairs >= min_pairs and tokens >= min_tokens and et_n >= min_et
    if need_splice:
        ok = ok and spliced
    if need_mine:
        ok = ok and bool(mined)
    return {
        "ok": ok,
        "conclusive": False,
        "identification": str(goal.get("identification") or "none"),
        "evidenceGrade": str(goal.get("evidenceGrade") or "heuristic"),
        "minLivePairs": min_pairs,
        "livePairs": pairs,
        "minSplicedTokens": min_tokens,
        "splicedTokens": tokens,
        "minEtiologies": min_et,
        "etiologies": et_n,
        "requireRespliceCausal": need_splice,
        "spliced": spliced,
        "mineFollowOn": need_mine,
        "mined": list(mined or []),
    }


def gate_verdict(spec: dict, *, details: dict, etiologies: list | None, text_column: str | None, error: str | None = None) -> dict:
    """Kineteq-style production gate on one pass: supported / review_required / refused / failed.

    refused  — no bound text column or no live cue pairs: nothing to pair, so do not invent.
    review   — live pairs but no catalogued candidate in them.
    supported — live pairs name at least one catalogued candidate. Still heuristic, still none.
    """
    rules = spec.get("gate") if isinstance(spec.get("gate"), dict) else {}
    if error:
        return {"verdict": "failed", "missing": [], "reason": str(error)[:200], "identification": "none"}
    pairs = int(details.get("livePairs") or 0)
    cands = {str(e.get("candidate") or "") for e in (etiologies or []) if e.get("candidate")}
    from_pairs = {str(e.get("candidate") or "") for e in (etiologies or []) if e.get("cue") and e.get("cue") != "catalog"}
    missing: list[str] = []
    if not text_column:
        missing.append("text_column")
    if pairs < int(rules.get("minLivePairs") or 1):
        missing.append("live_pairs")
    if not from_pairs:
        missing.append("catalogued_candidate_in_pair")
    if "text_column" in missing or "live_pairs" in missing:
        verdict = "refused"
    elif "catalogued_candidate_in_pair" in missing:
        verdict = "review_required"
    else:
        verdict = "supported"
    return {
        "verdict": verdict,
        "missing": missing,
        "livePairs": pairs,
        "candidates": sorted(cands),
        "identification": "none",
        "evidenceGrade": "heuristic",
        "note": str(rules.get("note") or "Gate on bound RelOp evidence. Supported means pairs named a catalogued candidate — not proof."),
    }
