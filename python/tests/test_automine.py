"""Automine runner: reflect on live corpus, expand catalogued follow-ons, mine again."""

from __future__ import annotations

from revolverelate.domain.gene import write_gene_pineal
from revolverelate.domain.mine import extract_targets
from revolverelate.domain.reflect import splice_question
from revolverelate.mcp.server import dispatch
from revolverelate.revolverelate import RevolveRelate
from revolverelate.vector.overlay import OVERLAY


def test_splice_question_keeps_cause_and_adds_details():
    ask = splice_question(
        "what causes pinealblastoma",
        {"symbols": ["DICER1", "DROSHA"], "cue": "because", "cause": "miRNA biogenesis is disrupted."},
        column="Evidence",
        family="gene",
    )
    assert "causes" in ask.casefold()
    assert "DICER1" in ask
    assert "because" in ask.casefold()
    assert "Evidence" in ask


def test_extract_targets_only_catalogued_follow_ons():
    hits = extract_targets(
        "DROSHA and DGCR8 mutations disrupt miRNA so PLAGL2/CCND2 are derepressed. Ignore FOOBAR1.",
        known={"DICER1", "RB1", "DROSHA", "DGCR8"},
    )
    symbols = {h["symbol"] for h in hits}
    assert symbols == {"PLAGL2", "CCND2"}
    already = extract_targets("PLAGL2 and DICER1", known={"PLAGL2", "DICER1", "CCND2"})
    assert already == []


def test_automine_reflects_then_mines_follow_ons(tmp_path):
    live = write_gene_pineal(tmp_path / "gene.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    before = {e.name for e in rr.schema.all_entities()}
    assert before == {"Gene", "Fasta", "Disease", "GeneDisease"}
    assert OVERLAY not in before
    symbols0 = {row[0] for row in rr.adapter.execute("SELECT Symbol FROM Gene")[1]}
    assert symbols0 == {"DICER1", "RB1", "DROSHA", "DGCR8"}
    state = rr.automine("what causes pinealblastoma", passes=3)
    assert state["kind"] == "automine"
    assert "PLAGL2" in state["mined"] or "CCND2" in state["mined"]
    assert state["passes"] >= 2
    assert state["stable"] is True
    assert state["stop"] in {"goalReached", "noNewTargets"}
    assert state.get("conclusive") is False
    assert state.get("identification") == "none"
    assert state.get("evidenceGrade") == "heuristic"
    assert state.get("domain") == "gene"
    assert state.get("evidenceKind") == "possible_etiology"
    assert state["gate"]["overall"] == "supported"
    assert all(e.get("gate") in {"supported", "review_required", "refused", "failed"} for e in state["etiologies"])
    assert state["memory"]["evidenceRows"] >= 1
    cands = set(state.get("candidates") or [])
    assert len(state.get("etiologies") or []) >= 3
    assert cands & {"DICER1", "DROSHA", "DGCR8", "RB1", "PLAGL2", "CCND2"}
    assert all(e.get("conclusive") is False for e in state.get("etiologies") or [])
    second = (state.get("history") or [None, None])[1]
    assert second and second.get("question") != "what causes pinealblastoma"
    assert "cause" in str(second.get("question") or "").casefold()
    assert second.get("splice", {}).get("symbols") or second.get("proposed")
    assert OVERLAY not in set(state["businessEntities"])
    symbols1 = {row[0] for row in rr.adapter.execute("SELECT Symbol FROM Gene")[1]}
    assert {"PLAGL2", "CCND2"} <= symbols1
    kpi = rr.kpi("cases_by_gene", live=True)
    live_syms = {str(row[0]) for row in (kpi.get("live") or {}).get("rows") or []}
    assert "PLAGL2" in live_syms or "CCND2" in live_syms
    state_path = tmp_path / ".revolverelate" / "automine.json"
    assert state_path.exists()
    rr.close()


def test_mcp_automine_loop(tmp_path, monkeypatch):
    live = write_gene_pineal(tmp_path / "gene.sqlite")
    monkeypatch.chdir(tmp_path)
    args = {"dsn": str(live), "workdir": str(tmp_path), "rows": 4}
    out = dispatch(
        "rr_automine",
        {**args, "question": "what causes this pinealblastoma genetic etiology", "passes": 3},
    )
    assert out.get("error") is None, out
    assert out["mode"] == "automine"
    assert out["stable"] is True
    assert out.get("conclusive") is False
    assert out.get("etiologies")
    assert out["mined"]
    assert any(row.get("added") for row in out.get("history") or [])
