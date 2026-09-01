"""Finance domain: offline equities writer, domain detection, gated automine, vector evidence memory."""

from __future__ import annotations

import sqlite3

from revolverelate.analytics.bind import bind_analytics_goal
from revolverelate.domain.evidence_store import EVIDENCE_ENTITY, evidence_stats, recall_evidence, remember_evidence
from revolverelate.domain.finance import bake_bars, cue_note, flag_moves, write_finance_equities
from revolverelate.domain.gene import write_gene_pineal
from revolverelate.domain.kpi import bind_kpis
from revolverelate.domain.mine import load_automine_spec
from revolverelate.domain.reflect import gate_verdict
from revolverelate.domain.registry import detect_domain, get_domain, list_domains
from revolverelate.mcp.server import MCP_TOOLS, dispatch
from revolverelate.revolverelate import RevolveRelate


def test_baked_bars_flag_injected_moves_only_after_window():
    bars = bake_bars("AAPL")
    assert len(bars) >= 200
    flagged = flag_moves(bars)
    moves = [r for r in flagged if r["isMove"]]
    assert moves, "seeded shocks must flag as moves"
    first_idx = flagged.index(moves[0])
    assert first_idx >= 20, "no z-score before the rolling window fills"
    big = [r for r in moves if r["absReturn"] >= 0.04]
    assert big, "the injected 4-7% shocks must be flagged"
    cue, note = cue_note("AAPL", big[0], after_event=None)
    assert cue in {"volume", "gap", "regime"}
    assert "because" in note or "therefore" in note
    assert "AAPL" in note


def test_writer_offline_is_baked_and_causal_ready(tmp_path):
    path = write_finance_equities(tmp_path / "eq.sqlite", use_yfinance=False)
    conn = sqlite3.connect(str(path))
    tickers = conn.execute("SELECT Symbol, Source, Peers FROM Ticker ORDER BY TickerId").fetchall()
    assert [t[0] for t in tickers] == ["AAPL", "MSFT", "NVDA", "JPM", "XOM"]
    assert {t[1] for t in tickers} == {"baked"}
    assert "MSFT" in tickers[0][2] and "GOOGL" in tickers[0][2]  # catalogued peers, incl. follow-ons
    assert conn.execute("SELECT COUNT(*) FROM PriceBar").fetchone()[0] >= 1000
    moves = conn.execute("SELECT COUNT(*) FROM PriceMove").fetchone()[0]
    assert 10 <= moves <= 120
    notes = [r[0] for r in conn.execute("SELECT Note FROM PriceMove")]
    assert all(("because" in n) or ("therefore" in n) for n in notes)
    assert conn.execute("SELECT COUNT(*) FROM MarketEvent").fetchone()[0] >= 5
    conn.close()


def test_domain_registry_detects_finance_and_gene(tmp_path):
    assert {d.id for d in list_domains()} >= {"gene", "finance"}
    fin = get_domain("finance")
    assert fin.evidence_kind == "possible_driver"
    assert fin.kpi == "abs_move_by_symbol"
    assert {"googl", "aapl"} <= set(fin.catalog())

    eq = write_finance_equities(tmp_path / "eq.sqlite", use_yfinance=False)
    rr = RevolveRelate.connect(str(eq), workdir=tmp_path / "w1")
    rr.build(rows_per_entity=6)
    assert detect_domain(rr.schema).id == "finance"
    goal = bind_analytics_goal(rr.schema, "what causes AAPL price moves")
    assert goal["measure"] == "AbsReturn"
    assert goal["dimension"] == "Symbol"
    assert goal["column"] == "Note"
    assert goal["treatment"] == "VolumeRatio"
    assert goal["slice"] == {"column": "Symbol", "value": "AAPL"}
    avail = {k["id"] for k in bind_kpis(rr.schema) if k["available"]}
    assert {"abs_move_by_symbol", "moves_by_direction", "top_moves", "abs_move_by_sector"} <= avail
    assert "cases_by_gene" not in avail
    rr.close()

    gene = write_gene_pineal(tmp_path / "gene.sqlite")
    rr = RevolveRelate.connect(str(gene), workdir=tmp_path / "w2")
    rr.build(rows_per_entity=4)
    assert detect_domain(rr.schema).id == "gene"
    rr.close()


def test_gate_verdicts_follow_kineteq_shape():
    spec = load_automine_spec()
    assert spec["gate"]["verdicts"] == ["supported", "review_required", "refused", "failed"]
    assert gate_verdict(spec, details={"livePairs": 0}, etiologies=[], text_column=None)["verdict"] == "refused"
    assert gate_verdict(spec, details={"livePairs": 3}, etiologies=[], text_column="Note")["verdict"] == "review_required"
    cat_only = [{"candidate": "GOOGL", "cue": "catalog"}]
    assert gate_verdict(spec, details={"livePairs": 3}, etiologies=cat_only, text_column="Note")["verdict"] == "review_required"
    ok = [{"candidate": "AAPL", "cue": "because"}]
    out = gate_verdict(spec, details={"livePairs": 3}, etiologies=ok, text_column="Note")
    assert out["verdict"] == "supported" and out["identification"] == "none"
    assert gate_verdict(spec, details={}, etiologies=[], text_column="Note", error="boom")["verdict"] == "failed"


def test_automine_finance_gates_remembers_and_reuses(tmp_path):
    eq = write_finance_equities(tmp_path / "eq.sqlite", use_yfinance=False)
    rr = RevolveRelate.connect(str(eq), workdir=tmp_path)
    rr.build(rows_per_entity=6)
    state = rr.automine("what causes AAPL price moves", passes=4)
    assert state["domain"] == "finance"
    assert state["evidenceKind"] == "possible_driver"
    assert state["reused"] is False
    assert state["gate"]["overall"] == "supported"
    assert state["stop"] in {"goalReached", "noNewTargets", "maxPasses"}
    assert state["conclusive"] is False and state["identification"] == "none"
    cands = set(state["candidates"])
    assert cands & {"AAPL", "MSFT", "NVDA", "JPM", "XOM"}
    drivers = {e.get("driver") for e in state["etiologies"] if e.get("driver")}
    assert drivers & {"volume spike", "trend regime", "earnings reaction", "opening gap"}
    first = state["history"][0]
    assert first["gate"]["verdict"] == "supported"
    assert first["etiologies"][0]["candidate"] == "AAPL"  # sliced ticker first
    assert first["remembered"] > 0
    # peers were expanded from live peer lists, catalogued only
    assert set(state["mined"]) <= {"GOOGL", "AMD", "META", "BAC", "CVX"}
    # vector evidence memory survived the rebuild after expansion
    mem = state["memory"]
    assert mem["entity"] == EVIDENCE_ENTITY and mem["evidenceRows"] >= 10
    later = [h for h in state["history"] if (h.get("recall") or {}).get("rowCount")]
    assert later, "later passes recall remembered evidence"
    rec = rr.recall("AAPL fell because volume ran", n=3)
    assert rec["ran"] and rec["rowCount"] >= 1
    assert all("Ticker" not in r["sourcePk"] for r in rec["rows"])
    causal_rec = rr.recall("high-volume down move", n=3, strategy="causal")
    assert causal_rec["ran"]
    # report is domain-labelled and cites only pipeline cards
    report = state["report"]
    assert report["title"].startswith("Possible drivers")
    kinds = {c["kind"] for c in report["citations"]}
    assert kinds <= {"relop_pair", "catalog_accession", "kpi_row"}
    assert any("finance.yahoo.com" in str(c.get("urls") or {}) for c in report["citations"])
    # same key, finished run → reused without mining again
    again = rr.automine("what causes AAPL price moves", passes=4, report=False)
    assert again["reused"] is True and again["reuseKey"] == state["reuseKey"]
    forced = rr.automine("what causes AAPL price moves", passes=1, report=False, rerun=True)
    assert forced["reused"] is False
    rr.close()


def test_remember_and_recall_roundtrip_without_automine(tmp_path):
    eq = write_finance_equities(tmp_path / "eq.sqlite", use_yfinance=False, symbols=["AAPL"])
    rr = RevolveRelate.connect(str(eq), workdir=tmp_path)
    rr.build(rows_per_entity=6)
    rows = [
        {"candidate": "AAPL", "cue": "because", "cause": "AAPL fell 6.1% on 2026-01-05", "effect": "volume ran 2.4x", "pass": 1, "source": {"pk": "7"}},
        {"candidate": "AAPL", "cue": "because", "cause": "AAPL fell 6.1% on 2026-01-05", "effect": "volume ran 2.4x", "pass": 1, "source": {"pk": "7"}},
    ]
    n = remember_evidence(rr.sandbox, rows, domain="finance", question="q", pass_no=1)
    assert n > 0
    assert remember_evidence(rr.sandbox, rows, domain="finance", question="q", pass_no=1) == 0  # idempotent
    stats = evidence_stats(rr.sandbox)
    assert stats["evidenceRows"] == 1
    out = recall_evidence(rr, "AAPL fell volume", n=2)
    assert out["ran"] and out["rowCount"] >= 1
    assert "AAPL" in out["rows"][0]["text"]
    rr.close()


def test_mcp_finance_tools(tmp_path, monkeypatch):
    names = {t["name"] for t in MCP_TOOLS}
    assert {"rr_finance", "rr_recall", "rr_automine", "rr_gene"} <= names
    monkeypatch.chdir(tmp_path)
    out = dispatch("rr_finance", {"workdir": str(tmp_path), "offline": True, "symbols": ["AAPL", "JPM"]})
    assert out["mode"] == "finance" and out["path"].endswith("equities.sqlite")
    conn = sqlite3.connect(out["path"])
    assert [r[0] for r in conn.execute("SELECT Symbol FROM Ticker ORDER BY TickerId")] == ["AAPL", "JPM"]
    conn.close()
    args = {"dsn": out["path"], "workdir": str(tmp_path), "rows": 6}
    mined = dispatch("rr_automine", {**args, "question": "what causes JPM price moves", "passes": 2})
    assert mined.get("error") is None, mined
    assert mined["domain"] == "finance"
    assert mined["gate"]["overall"] in {"supported", "review_required"}
    assert mined["history"][0]["gate"]["verdict"]
    rec = dispatch("rr_recall", {**args, "query": "JPM fell because"})
    assert rec["mode"] == "recall" and rec["ran"]
