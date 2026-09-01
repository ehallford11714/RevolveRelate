"""Domain registry for automine. A domain is a spec/domain-*.json plus a small adapter.

Detection binds on columns that exist in the booted schema. The runner never
hardcodes gene or finance names; it asks the domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from revolverelate.analytics.bind import resolve_column
from revolverelate.domain.kpi import load_domain_specs
from revolverelate.domain.mine import catalog_targets
from revolverelate.schema.model import SchemaGraph

_DEFAULT_AUTOMINE = {
    "detect": [],
    "symbolEntity": "",
    "symbolColumn": "Symbol",
    "kpi": "",
    "scanEntities": [],
    "pivotColumns": ["Abstract", "Evidence", "Summary", "Header"],
    "families": ["gene", "epitope", "sirna"],
    "evidenceKind": "possible_etiology",
    "evidenceLabel": "possible etiology",
    "candidateLabel": "candidate",
    "defaultQuestion": "what causes this",
    "citationLocator": "spec/domain.json",
}


@dataclass
class Domain:
    id: str
    spec: dict
    automine: dict = field(default_factory=dict)

    @property
    def evidence_kind(self) -> str:
        return str(self.automine.get("evidenceKind") or "possible_etiology")

    @property
    def evidence_label(self) -> str:
        return str(self.automine.get("evidenceLabel") or "possible etiology")

    @property
    def candidate_label(self) -> str:
        return str(self.automine.get("candidateLabel") or "candidate")

    @property
    def kpi(self) -> str:
        return str(self.automine.get("kpi") or "")

    @property
    def scan_entities(self) -> list[str]:
        return [str(x) for x in self.automine.get("scanEntities") or []]

    @property
    def pivot_columns(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.automine.get("pivotColumns") or _DEFAULT_AUTOMINE["pivotColumns"])

    @property
    def families(self) -> tuple[str, ...]:
        return tuple(str(x) for x in self.automine.get("families") or _DEFAULT_AUTOMINE["families"])

    @property
    def sources(self) -> dict:
        return self.spec.get("sources") if isinstance(self.spec.get("sources"), dict) else {}

    @property
    def honesty(self) -> str:
        return str(self.spec.get("honesty") or "")

    def catalog(self) -> dict[str, dict]:
        return catalog_targets(self.spec)

    def default_question(self) -> str:
        return str(self.automine.get("defaultQuestion") or _DEFAULT_AUTOMINE["defaultQuestion"])

    def known(self, conn) -> set[str]:
        entity = str(self.automine.get("symbolEntity") or "")
        column = str(self.automine.get("symbolColumn") or "Symbol")
        if conn is None or not entity:
            return set()
        try:
            rows = conn.execute(f'SELECT "{column}" FROM "{entity}"').fetchall()
        except Exception:
            return set()
        return {str(r[0]) for r in rows if r and r[0]}

    def append_follow_on(self, conn, records: list[dict]) -> list[str]:
        if conn is None or not records:
            return []
        if self.id == "gene":
            from revolverelate.domain.gene import append_follow_on

            return append_follow_on(conn, records)
        if self.id == "finance":
            from revolverelate.domain.finance import append_follow_on

            return append_follow_on(conn, records)
        return []


def _has(graph: SchemaGraph, name: str) -> bool:
    try:
        resolve_column(graph, name)
        return True
    except Exception:
        return False


def list_domains() -> list[Domain]:
    out = []
    for spec in load_domain_specs():
        if str(spec.get("kind") or "domain") != "domain":
            continue
        auto = {**_DEFAULT_AUTOMINE, **(spec.get("automine") if isinstance(spec.get("automine"), dict) else {})}
        out.append(Domain(id=str(spec.get("id") or ""), spec=spec, automine=auto))
    return out


def get_domain(domain_id: str) -> Domain:
    for d in list_domains():
        if d.id.casefold() == str(domain_id).casefold():
            return d
    raise KeyError(f"Unknown domain {domain_id!r}. Known: {[d.id for d in list_domains()]}")


def detect_domain(graph: SchemaGraph, *, prefer: str | None = None) -> Domain | None:
    """Pick the domain whose detect columns all exist. Most specific (most columns) wins."""
    if prefer:
        try:
            return get_domain(prefer)
        except KeyError:
            pass
    best: Domain | None = None
    best_n = 0
    for d in list_domains():
        cols = [str(c) for c in d.automine.get("detect") or []]
        if not cols:
            continue
        if all(_has(graph, c) for c in cols) and len(cols) > best_n:
            best, best_n = d, len(cols)
    return best
