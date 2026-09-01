"""Citation-grounded report after automine. SLM is optional; tests stay offline."""

from __future__ import annotations

from revolverelate.domain.citations import collect_citations, format_reference, strip_unknown_cites
from revolverelate.domain.gene import write_gene_pineal
from revolverelate.domain.research import run_research, validate_draft
from revolverelate.mcp.server import dispatch
from revolverelate.revolverelate import RevolveRelate


def _handoff_state() -> dict:
    return {
        "kind": "automine",
        "question": "what causes pinealblastoma",
        "finalQuestion": "what causes pinealblastoma DICER1 because Evidence",
        "candidates": ["DICER1", "DROSHA", "PLAGL2"],
        "mined": ["PLAGL2"],
        "passes": 2,
        "stop": "goalReached",
        "identification": "none",
        "evidenceGrade": "heuristic",
        "conclusive": False,
        "honesty": "Possible etiology evidence from bound RelOp pairs and catalogued genes. Identification is none.",
        "etiologies": [
            {
                "candidate": "DICER1",
                "kind": "possible_etiology",
                "identification": "none",
                "evidenceGrade": "heuristic",
                "conclusive": False,
                "cue": "because",
                "cause": "Germline DICER1 mutation causes DICER1 syndrome.",
                "effect": "pineoblastoma risk rises",
                "source": {"entity": "Disease", "column": "Abstract", "pk": "1"},
            },
            {
                "candidate": "DROSHA",
                "kind": "possible_etiology",
                "identification": "none",
                "conclusive": False,
                "cue": "because",
                "cause": "pri-miRNA cleavage fails",
                "effect": "let-7 declines",
                "source": {"entity": "Gene", "column": "Summary", "pk": "3"},
            },
        ],
        "history": [
            {
                "kpi": {
                    "id": "cases_by_gene",
                    "live": {
                        "columns": ["Symbol", "Cases"],
                        "rows": [["DICER1", 68], ["DROSHA", 12]],
                    },
                }
            }
        ],
    }


def test_citations_are_pipeline_only():
    cards = collect_citations(_handoff_state())
    kinds = {c["kind"] for c in cards}
    assert kinds <= {"relop_pair", "catalog_accession", "kpi_row"}
    assert {c["id"] for c in cards} == {f"E{i}" for i in range(1, len(cards) + 1)}
    assert any(c["kind"] == "relop_pair" and c["candidate"] == "DICER1" for c in cards)
    catalog = [c for c in cards if c["kind"] == "catalog_accession" and c["candidate"] == "DICER1"]
    assert catalog
    assert "ncbiGene" in (catalog[0].get("urls") or {})
    assert catalog[0]["locator"].startswith("spec/domain-gene.json")
    assert any(c["kind"] == "kpi_row" and c["candidate"] == "DICER1" for c in cards)
    ref = format_reference(catalog[0])
    assert "[E" in ref
    assert "Identification: none" in ref


def test_validator_strips_unknown_citation_ids():
    cards = collect_citations(_handoff_state())
    allowed = {c["id"] for c in cards}
    dirty, unknown = strip_unknown_cites("DICER1 [E1] invented [E99] and [E2].", allowed)
    assert "E99" in unknown
    assert "[E99]" not in dirty
    assert "[E1]" in dirty
    checked = validate_draft(
        "We discovered the cause [E99]. DICER1 [E1].",
        cards,
        {"honesty": "Possible etiology evidence. Identification is none."},
    )
    assert "E99" in checked["unknownCitations"]
    assert "[E99]" not in checked["markdown"]
    assert "Honesty" in checked["markdown"]
    assert checked["conclusive"] is False
    assert checked["identification"] == "none"
    assert "discovered the cause" in " ".join(checked["forbidden"])


def test_deterministic_report_from_handoff(tmp_path):
    report = run_research(_handoff_state(), workdir=tmp_path, use_slm=False)
    assert report["kind"] == "research_report"
    assert report["conclusive"] is False
    assert report["identification"] == "none"
    roles = [a["agent"] for a in report["agents"]]
    assert roles == ["planner", "researcher", "reporter", "validator"]
    assert report["agents"][2]["backend"] == "deterministic"
    md = report["markdown"]
    assert "Possible etiologies" in md or "possible etiologies" in md.casefold()
    assert "[E1]" in md
    assert "https://www.ncbi.nlm.nih.gov/gene/" in md
    assert "not conclusive proof" in md.casefold() or "Identification is none" in md
    assert (tmp_path / ".revolverelate" / "report.md").exists()
    cited = set(report["validation"]["cited"])
    allowed = {c["id"] for c in report["citations"]}
    assert cited <= allowed
    assert report["validation"]["unknownCitations"] == []


def test_automine_wires_report(tmp_path):
    live = write_gene_pineal(tmp_path / "gene.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    state = rr.automine("what causes pinealblastoma", passes=3)
    report = state.get("report") or {}
    assert report.get("kind") == "research_report"
    assert report.get("conclusive") is False
    assert report.get("citations")
    assert "[E1]" in (report.get("markdown") or "")
    assert (tmp_path / ".revolverelate" / "report.md").exists()
    again = rr.report()
    assert again["kind"] == "research_report"
    assert again["citations"]
    rr.close()


def test_mcp_report_from_saved_handoff(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dest = tmp_path / ".revolverelate"
    dest.mkdir(parents=True)
    (dest / "automine.json").write_text(
        __import__("json").dumps(_handoff_state()),
        encoding="utf-8",
    )
    out = dispatch("rr_report", {"workdir": str(tmp_path)})
    assert out.get("error") is None, out
    assert out["mode"] == "report"
    assert out["conclusive"] is False
    assert out["citations"]
    assert out["markdown"]
    assert {a["agent"] for a in out["agents"]} >= {"planner", "researcher", "reporter", "validator"}
