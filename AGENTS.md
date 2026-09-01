# RevolveRelate agent notes

- Source of truth is `spec/`. Do not emit SQL from an SLM.
- `build()` writes `.revolverelate/build.json`. Live `promote` is illegal until `status=complete` and sandbox validation is saved.
- Dummy sandbox never copies live critical/pii values.
- Python and TypeScript compilers must match `spec/fixtures/*.json` byte-for-byte.
- Warehouse engines in the catalog compile always; live execute is tier A only.
- MCP: `rr_boot` any catalogued DSN (parse schema, dummy stage, auto-chunk non-PII text). Atoms bind from that schema — not Superstore-only. Chain RelOps; `rr_question` any business, retrieve, or causal question (including “what causes …”) — dummy ticket, then the same RelOp on live. Never emit SQL. OverlayChunk is virtual; live overlay is TEMP from live text only.
- Tutorial: `docs/tutorial.md` and `python -m revolverelate tutorial`. Superstore facts, overlay RAG/semantic/causal chunks, then gene automine + cited report.
- Gene / FASTA domain: `spec/domain-gene.json` + `python -m revolverelate gene`. Public NCBI protein FASTA (DICER1/RB1/DROSHA/DGCR8) plus abstracts. Bind KPIs (`cases_by_gene`, …) when those columns exist. Ask “what causes pinealblastoma” — RelOp + overlay cues, not a discovery claim.
- Automine: `spec/automine.json` + `python -m revolverelate automine`. Each pass re-causes, collects **possible etiology evidence** (heuristic, identification none — not proof), splices cues/genes, pivots, expands catalogued `followOn`, until enough evidence rows. MCP: `rr_automine`, `rr_kpi`, `rr_gene`.
- Deep research report: `spec/deep-research.json` after automine. Planner → researcher → reporter → validator. Citations are RelOp pairs, catalog NCBI/UniProt accessions, and bound KPI rows only — never invented papers. Local SLM (Ollama / OpenAI-compatible) or cloud API if `REVOLVERELATE_API_KEY` is set; otherwise a deterministic draft. MCP: `rr_report`. CLI: `python -m revolverelate report`. Identification remains none.
