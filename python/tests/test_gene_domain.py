"""Public FASTA gene domain: bind KPIs and ask what causes pinealblastoma."""

from __future__ import annotations

from revolverelate.analytics.bind import bind_analytics_goal
from revolverelate.analytics.causal_plan import fallback_causal_plan, match_causal_composite
from revolverelate.analytics.heuristic import bind_because
from revolverelate.domain.fasta import PUBLIC_PROTEIN_FASTA, parse_fasta
from revolverelate.domain.gene import write_gene_pineal
from revolverelate.domain.kpi import bind_kpis
from revolverelate.mcp.server import dispatch, route_question
from revolverelate.revolverelate import RevolveRelate
from revolverelate.vector.overlay import OVERLAY


def test_parse_public_ncbi_fasta():
    recs = parse_fasta(PUBLIC_PROTEIN_FASTA)
    assert {r["header"].split()[0] for r in recs} >= {"NP_803187.1", "NP_000312.2", "NP_037367.3", "NP_073557.3"}
    assert all(r["length"] >= 60 for r in recs)
    assert recs[0]["sequence"].startswith("MKSPAL")


def test_gene_schema_binds_pinealblastoma_and_kpis(tmp_path):
    live = write_gene_pineal(tmp_path / "gene.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    names = {e.name for e in rr.schema.all_entities()}
    assert names == {"Gene", "Fasta", "Disease", "GeneDisease"}
    assert OVERLAY not in names
    bound = bind_analytics_goal(rr.schema, "what causes pinealblastoma")
    assert bound["measure"] == "Cases"
    assert bound["dimension"] == "DiseaseName"
    assert bound["column"] in {"Abstract", "Evidence", "Summary"}
    assert bound["treatment"] in {"LoFCount", "AssociationScore"}
    kpis = {k["id"]: k for k in bind_kpis(rr.schema)}
    assert kpis["cases_by_gene"]["available"] is True
    assert kpis["share_of_cases"]["recipe"] == "share_of_total"
    listed = rr.analytics.list()
    assert any(k["id"] == "cases_by_gene" and k["available"] for k in listed.get("kpis") or [])
    rr.close()


def test_gene_causal_relop_dummy_then_live(tmp_path):
    live = write_gene_pineal(tmp_path / "gene.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    assert match_causal_composite("what causes pinealblastoma") == "rag_causal_pair"
    plan = fallback_causal_plan("what causes pinealblastoma", rr.schema)
    assert plan["grammar"]["ok"]
    assert any(s.get("op") == "chunk_causal" and s.get("column") in {"Abstract", "Evidence", "Summary"} for s in plan["steps"])
    ran = rr.analytics.run_chain(plan["steps"], plan_id="pineal-causes")
    assert ran["status"] == "sandbox_ok"
    live_out = rr.replay_live(plan_id=ran["id"])
    assert live_out["ran"] is True
    overlay = rr.sandbox.execute(f'SELECT Text, Cue, Role, "Column" FROM "{OVERLAY}"')[1]
    blob = " ".join(str(v) for row in overlay for v in row).casefold()
    assert "because" in blob or "therefore" in blob or "caused" in blob
    assert "dicer" in blob or "np_803187" in blob
    causal = rr.causal("what causes pinealblastoma", live=True)
    assert causal["relop"]["status"] == "sandbox_ok"
    assert causal["goal"]["measure"] == "Cases"
    assert causal["live"]["ran"] is True
    rr.close()


def test_gene_kpi_cases_by_symbol_promotes(tmp_path):
    live = write_gene_pineal(tmp_path / "gene.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    out = rr.kpi("cases_by_gene", live=True)
    assert out["status"] == "sandbox_ok"
    assert out["rowCount"]
    assert (out["live"] or {}).get("ran") is True
    cols = [str(c).casefold() for c in out.get("columns") or []]
    assert any("symbol" in c for c in cols)
    rr.close()


def test_gene_heuristic_binds_without_superstore_columns(tmp_path):
    live = write_gene_pineal(tmp_path / "gene.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=4)
    bind = bind_because(rr.schema, "what causes pinealblastoma because germline mutation")
    assert bind["outcome"] == "Cases"
    assert bind["treatment"] == "LoFCount"
    assert bind.get("slice", {}).get("column") != "Region"
    rr.close()


def test_mcp_gene_boot_and_ask_causes(tmp_path, monkeypatch):
    live = write_gene_pineal(tmp_path / "gene.sqlite")
    monkeypatch.chdir(tmp_path)
    args = {"dsn": str(live), "workdir": str(tmp_path), "rows": 4}
    assert route_question("what causes pinealblastoma") == "causal"
    assert route_question("cases by gene KPI") == "kpi"
    assert route_question("nearest DICER1 FASTA") == "rag"
    booted = dispatch("rr_boot", args)
    assert booted["ok"] is True
    assert "Cases" in booted["measures"]
    assert any(k.get("id") == "cases_by_gene" and k.get("available") for k in booted.get("kpis") or [])
    asked = dispatch("rr_question", {**args, "question": "what causes pinealblastoma"})
    assert asked.get("error") is None, asked
    assert asked.get("routed") == "causal"
    assert (asked.get("relop") or {}).get("status") == "sandbox_ok"
    kpi = dispatch("rr_question", {**args, "question": "cases by gene KPI"})
    assert kpi.get("error") is None, kpi
    assert kpi.get("routed") == "kpi"
    assert kpi.get("status") == "sandbox_ok"
