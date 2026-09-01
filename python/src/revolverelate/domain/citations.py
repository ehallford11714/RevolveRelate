"""Citation cards from automine findings. Never invent papers or web sources."""

from __future__ import annotations

import re

from revolverelate.domain.mine import catalog_targets, domain_catalog

CITE_RE = re.compile(r"\[(E\d+)\]")
_FORBID = (
    "we discovered",
    "discovered the cause",
    "identified the cause",
    "proves that",
    "this is proof",
    "conclusive identification",
)


def load_research_spec() -> dict:
    from revolverelate.catalog import spec_dir
    import json

    return json.loads((spec_dir() / "deep-research.json").read_text(encoding="utf-8"))


def _urls_for(row: dict, sources: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    quote_tmpl = str(sources.get("yahooQuote") or "")
    if quote_tmpl and row.get("symbol") and not row.get("ncbiGeneId") and not row.get("protein"):
        out["quote"] = quote_tmpl.format(symbol=row["symbol"])
        hist_tmpl = str(sources.get("yahooHistory") or "")
        if hist_tmpl:
            out["history"] = hist_tmpl.format(symbol=row["symbol"])
        return out
    gene_id = str(row.get("ncbiGeneId") or "").strip()
    protein = str(row.get("protein") or "").strip()
    uniprot = str(row.get("uniprot") or "").strip()
    gene_tmpl = str(sources.get("ncbiGene") or "https://www.ncbi.nlm.nih.gov/gene/{id}")
    fetch_tmpl = str(sources.get("ncbiEfetch") or "")
    if gene_id:
        out["ncbiGene"] = gene_tmpl.format(id=gene_id)
    if protein:
        out["protein"] = f"https://www.ncbi.nlm.nih.gov/protein/{protein}"
        if fetch_tmpl:
            out["fasta"] = fetch_tmpl.format(accession=protein)
    if uniprot:
        out["uniprot"] = f"https://www.uniprot.org/uniprotkb/{uniprot}"
    return out


def _next_id(n: int) -> str:
    return f"E{n}"


def collect_citations(state: dict | None) -> list[dict]:
    """Build numeric [E#] cards from etiologies, catalog accessions, and KPI rows."""
    state = state if isinstance(state, dict) else {}
    domain_id = str(state.get("domain") or "gene")
    spec = domain_catalog(domain_id) or domain_catalog()
    sources = spec.get("sources") if isinstance(spec.get("sources"), dict) else {}
    catalog = catalog_targets(spec)
    auto = spec.get("automine") if isinstance(spec.get("automine"), dict) else {}
    locator_base = str(auto.get("citationLocator") or f"spec/domain-{domain_id}.json")
    cand_label = str(auto.get("candidateLabel") or "candidate")
    out: list[dict] = []
    seen: set[str] = set()
    n = 1

    def add(card: dict) -> None:
        nonlocal n
        key = str(card.get("key") or card.get("locator") or card.get("id") or "")
        if not key or key in seen:
            return
        seen.add(key)
        card.pop("key", None)
        card["id"] = _next_id(n)
        n += 1
        out.append(card)

    for row in state.get("etiologies") or []:
        if not isinstance(row, dict):
            continue
        src = row.get("source") if isinstance(row.get("source"), dict) else {}
        entity = str(src.get("entity") or "")
        column = str(src.get("column") or "")
        pk = str(src.get("pk") or "")
        locator = f"{entity}.{column}#{pk}" if entity else f"etiology#{row.get('candidate')}"
        span = " ".join(p for p in (row.get("cause"), row.get("effect"), row.get("cue")) if p).strip()
        add(
            {
                "key": f"relop|{locator}|{row.get('candidate')}|{row.get('cue')}",
                "kind": "relop_pair",
                "candidate": str(row.get("candidate") or ""),
                "title": f"Live RelOp pair: {row.get('candidate') or 'unknown'} / {row.get('cue') or 'cue'}",
                "span": span,
                "locator": locator,
                "source": src,
                "cue": str(row.get("cue") or ""),
                "evidenceGrade": "heuristic",
                "identification": "none",
                "conclusive": False,
            }
        )

    symbols: list[str] = []
    for name in list(state.get("candidates") or []) + list(state.get("mined") or []):
        if name and name.casefold() not in {s.casefold() for s in symbols}:
            symbols.append(str(name))
    for name in symbols:
        rec = catalog.get(name.casefold())
        if not rec:
            continue
        protein = str(rec.get("protein") or "")
        noun = "accession" if (protein or rec.get("ncbiGeneId")) else cand_label
        add(
            {
                "key": f"catalog|{name.casefold()}",
                "kind": "catalog_accession",
                "candidate": str(rec.get("symbol") or name),
                "title": f"Catalogued {noun} {rec.get('symbol') or name}"
                + (f" ({protein})" if protein else (f" ({rec.get('name')})" if rec.get("name") else "")),
                "span": str(rec.get("evidence") or rec.get("summary") or rec.get("role") or rec.get("sector") or ""),
                "locator": f"{locator_base}#{rec.get('symbol') or name}",
                "urls": _urls_for(rec, sources),
                "ncbiGeneId": str(rec.get("ncbiGeneId") or ""),
                "protein": protein,
                "uniprot": str(rec.get("uniprot") or ""),
                "evidenceGrade": "heuristic",
                "identification": "none",
                "conclusive": False,
            }
        )

    kpi = _last_kpi(state)
    live = (kpi.get("live") if isinstance(kpi, dict) else None) or {}
    rows = list(live.get("rows") or (kpi.get("rows") if isinstance(kpi, dict) else None) or [])
    cols = [str(c) for c in (live.get("columns") or [])]
    kpi_id = str((kpi or {}).get("id") or "kpi")
    for i, row in enumerate(rows):
        cells = list(row) if not isinstance(row, dict) else list(row.values())
        label = str(cells[0] if cells else i)
        blob = " ".join(str(v) for v in cells if v is not None)
        add(
            {
                "key": f"kpi|{kpi_id}|{label.casefold()}",
                "kind": "kpi_row",
                "candidate": label,
                "title": f"Bound KPI {kpi_id}: {label}",
                "span": blob,
                "locator": f"kpi:{kpi_id}#{label}",
                "kpi": kpi_id,
                "columns": cols,
                "evidenceGrade": "heuristic",
                "identification": "none",
                "conclusive": False,
            }
        )
    return out


def _last_kpi(state: dict) -> dict | None:
    for row in reversed(list(state.get("history") or [])):
        if isinstance(row, dict) and isinstance(row.get("kpi"), dict):
            return row["kpi"]
    return None


def citation_index(citations: list[dict]) -> dict[str, dict]:
    return {str(c.get("id")): c for c in citations if c.get("id")}


def cited_ids(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in CITE_RE.findall(text or ""):
        if match not in seen:
            seen.add(match)
            found.append(match)
    return found


def strip_unknown_cites(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    unknown: list[str] = []

    def repl(match: re.Match) -> str:
        cid = match.group(1)
        if cid in allowed:
            return match.group(0)
        unknown.append(cid)
        return ""

    cleaned = CITE_RE.sub(repl, text or "")
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned, unknown


def format_reference(card: dict) -> str:
    cid = str(card.get("id") or "")
    title = str(card.get("title") or "Untitled source")
    locator = str(card.get("locator") or "")
    urls = card.get("urls") if isinstance(card.get("urls"), dict) else {}
    url = ""
    if urls:
        url = str(urls.get("ncbiGene") or urls.get("protein") or urls.get("uniprot") or urls.get("fasta") or urls.get("quote") or "")
    span = str(card.get("span") or "").strip()
    parts = [f"[{cid}] {title}."]
    if locator:
        parts.append(f"Locator: {locator}.")
    if url:
        parts.append(url)
    if span:
        parts.append(f'Span: "{span}"')
    parts.append("Evidence grade: heuristic. Identification: none.")
    return " ".join(parts)


def forbidden_hits(text: str) -> list[str]:
    low = (text or "").casefold()
    return [p for p in _FORBID if p in low]
