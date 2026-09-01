<p align="center">
  <img src="docs/brand/spheres.svg" width="220" alt="Two spheres rotating inside each other">
</p>

<p align="center">
  <img src="docs/brand/revolverelate-spheres.png" width="280" alt="RevolveRelate nested spheres">
</p>

# RevolveRelate

Ask a database a question in plain English. The engine turns that into **relational algebra**, compiles it to SQL, and tries it first on a **safe dummy copy** of your schema. Only after that dummy run succeeds can the same plan run on the live database.

The two spheres are the idea: an inner working copy turning inside the outer live world. They stay related, but they do not mix until you promote.

The language model never writes SQL. It never invents paper citations. Identification of a cause stays **none** — evidence, not proof.

## What it is for

- **Talk to any catalogued database** (SQLite, Postgres, MySQL, warehouses) without pasting passwords into generated SQL.
- **Keep live PII off the dummy.** Emails and secrets are masked in the sandbox.
- **Retrieve meaning, not just rows.** Text is split into OverlayChunk units (semantic, causal, topic, event, …) so you can ask “nearest bookcase binders” or “sales fell because discounting.”
- **Mine a literature-style corpus** for *possible* etiologies, then draft a report grounded in RelOp pairs, catalog NCBI/UniProt accessions, and bound KPIs — not invented papers.

## What it can do

| You want | Command or tool |
| --- | --- |
| Walk Superstore, RAG chunks, then the autominer | `python -m revolverelate tutorial --root ./tutorial-run` |
| Write the Superstore sample and ask a question | `python -m revolverelate superstore` then `ask "customers in West"` |
| Semantic or causal retrieve | `python -m revolverelate rag "bookcase binders" --dsn ./superstore.sqlite` |
| Possible etiologies + cited report | `python -m revolverelate automine --question "what causes pinealblastoma"` |
| Agent loop in Cursor / VS Code / Claude | `rr_boot` → `rr_question` / `rr_rag` → `rr_automine` → `rr_report` |

Guided write-up: [docs/tutorial.md](docs/tutorial.md).

## Flow

1. `connect` a database (or provide a schema).
2. `build` **once** — introspect, write the graph, create a local sandbox of dummy rows (critical fields masked).
3. `ask` — English becomes RelOp, RelOp becomes SQL, SQL runs on the dummy sandbox.
4. `analytics` / `rag` / `causal` — named recipes, OverlayChunk retrieve, or a why-question. Dummy first; the same RelOp can replay live.
5. `promote` — live only if the build cache is complete and the sandbox run was saved.
6. `automine` then `report` — on a catalogued corpus, collect possible etiology evidence and draft a citation-bound report. Not a discovery claim.

```bash
pip install -e python
python -m revolverelate tutorial --root ./tutorial-run
python -m revolverelate superstore --dest ./superstore.sqlite
python -m revolverelate ask "customers in West" --dsn ./superstore.sqlite
python -m revolverelate rag "bookcase binders" --dsn ./superstore.sqlite --strategy semantic
python -m revolverelate automine --question "what causes pinealblastoma"
python -m revolverelate.mcp --install
```

Vite / Streamlit Superstore UI: [demo/README.md](demo/README.md). MCP hosts: [docs/mcp.md](docs/mcp.md). Superstore numbers: [docs/examples-superstore.md](docs/examples-superstore.md).

## Packages

- `spec/` — JSON Schemas, engine catalog, golden RelOp→SQL fixtures
- `python/revolverelate` — connect, build, sandbox, policy, promote, CLI
- `typescript/@revolverelate/core` — same compiler, bun/npm CLI
