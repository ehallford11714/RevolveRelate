"""Live pinealblastoma ask through RevolveRelate. RelOp only; no SLM SQL."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ["REVOLVERELATE_SLM"] = "0"
os.environ.pop("REVOLVERELATE_CHROMA", None)

from revolverelate.analytics.bind import bind_analytics_goal, list_dimensions, list_measures
from revolverelate.domain.gene import write_gene_pineal
from revolverelate.domain.kpi import bind_kpis
from revolverelate.mcp.server import dispatch, route_question
from revolverelate.revolverelate import RevolveRelate
from revolverelate.slm.jobs import schema_card
from revolverelate.vector.overlay import OVERLAY

QUESTION = "what causes this pinealblastoma genetic etiology"
ROOT = Path(__file__).resolve().parent
LIVE = ROOT / "gene.sqlite"
WORKDIR = ROOT / "gene-live"


def main() -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    write_gene_pineal(LIVE)
    rr = RevolveRelate.connect(str(LIVE), workdir=WORKDIR)
    built = rr.build(rows_per_entity=8, refresh=True)
    graph = rr.schema
    overlay_entity = graph.entity(OVERLAY)
    schema = {
        "engine": graph.engine,
        "dialect": graph.dialect,
        "businessEntities": [e.to_dict() for e in graph.all_entities()],
        "virtualOverlay": overlay_entity.to_dict() if overlay_entity else None,
        "relationships": [r.to_dict() for r in graph.relationships],
        "card": schema_card(graph, rr.policy),
        "measures": list_measures(graph),
        "dimensions": list_dimensions(graph),
        "kpis": bind_kpis(graph),
        "overlay": rr.overlay_stats(),
        "policyAttributes": (rr.policy or {}).get("attributes") or {},
        "allEntitiesExcludesOverlay": OVERLAY not in {e.name for e in graph.all_entities()},
    }
    bound = bind_analytics_goal(graph, QUESTION)
    causal = rr.causal(QUESTION, live=True)
    kpi = rr.kpi("cases_by_gene", live=True)
    dummy_overlay = rr.sandbox.execute(
        f'SELECT Entity, "Column", Strategy, Cue, Role, substr(Text,1,160) FROM "{OVERLAY}" '
        "WHERE Strategy = ? ORDER BY Cue DESC",
        ["causal"],
    )
    live_tables = {}
    for table in ("Gene", "Disease", "GeneDisease", "Fasta"):
        cols, rows = rr.adapter.execute(f'SELECT * FROM "{table}"')
        if table == "Fasta":
            idx = {c: i for i, c in enumerate(cols)}
            seq_i = idx.get("Sequence")
            slim = []
            for row in rows:
                item = list(row)
                if seq_i is not None and item[seq_i]:
                    item[seq_i] = f"{str(item[seq_i])[:40]}… ({len(str(item[seq_i]))} aa)"
                slim.append(item)
            rows = slim
        live_tables[table] = {"columns": cols, "rows": rows}

    mcp_args = {"dsn": str(LIVE), "workdir": str(WORKDIR), "rows": 8}
    asked = dispatch("rr_question", {**mcp_args, "question": QUESTION})
    payload = {
        "question": QUESTION,
        "routed": route_question(QUESTION),
        "livePath": str(LIVE),
        "build": {k: built.get(k) for k in ("status", "engine") if k in built} or {"status": "complete"},
        "bound": bound,
        "schema": schema,
        "causal": {
            "composite": causal.get("composite"),
            "goal": causal.get("goal"),
            "grammar": causal.get("grammar"),
            "dummyStatus": (causal.get("relop") or {}).get("status"),
            "dummyColumns": (causal.get("relop") or {}).get("columns"),
            "dummyRows": (causal.get("relop") or {}).get("rows"),
            "dummyRowCount": (causal.get("relop") or {}).get("rowCount"),
            "live": causal.get("live"),
        },
        "kpi_cases_by_gene": {
            "status": kpi.get("status"),
            "columns": kpi.get("columns"),
            "rows": kpi.get("rows"),
            "live": kpi.get("live"),
        },
        "dummyCausalOverlay": {"columns": dummy_overlay[0], "rows": dummy_overlay[1][:24]},
        "liveTables": live_tables,
        "mcpQuestion": {
            "routed": asked.get("routed"),
            "mode": asked.get("mode"),
            "goal": asked.get("goal"),
            "relop": asked.get("relop"),
            "live": asked.get("live"),
            "error": asked.get("error"),
        },
        "honesty": (
            "Bound RelOp + public NCBI FASTA/abstracts. Not a claim that RevolveRelate "
            "discovered pineoblastoma etiology."
        ),
    }
    out = WORKDIR / "pinealblastoma-live.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(out)
    rr.close()


if __name__ == "__main__":
    main()
