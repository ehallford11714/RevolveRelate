# RevolveRelate MCP

Dedicated Model Context Protocol server so **any agent host** can install the package, link **any catalogued database**, and ask **any business question** — without inventing SQL.

```powershell
pip install -e python
python -m revolverelate.mcp --install   # host JSON for Cursor / VS Code / Claude / Windsurf
python -m revolverelate.mcp             # stdio server
```

Repo already ships host files:

| Host | Path |
| --- | --- |
| Cursor | [`.cursor/mcp.json`](../.cursor/mcp.json) |
| VS Code | [`.vscode/mcp.json`](../.vscode/mcp.json) |
| Claude Code | [`.mcp.json`](../.mcp.json) |
| Windsurf | [`.windsurf/mcp.json`](../.windsurf/mcp.json) |
| Generic | [`mcp.json`](../mcp.json) |

Claude Desktop uses the same `mcpServers` block in its app-support `claude_desktop_config.json` (absolute paths; `rr_install` prints them).

Set `REVOLVERELATE_DSN` and `REVOLVERELATE_WORKDIR` if you want a default database.

## Agent loop

1. `rr_install` — host JSON (once per machine). No database required.
2. `rr_boot {dsn}` — attach **any** catalogued DSN (`./app.sqlite`, `postgresql://…`, `mysql://…`, `snowflake://…`, `bigquery://…`). Builds the dummy sandbox once. Does not copy live PII.
3. `rr_question {question}` — English, or a named `recipe`, or a named `composite` / `steps`. RelOp only. Runs on the dummy sandbox.
4. `rr_analytics_primitives` if you need atoms to chain (24 families including vector RAG, Socratic intent, and RelOp ideation, max depth 24).
5. `rr_rag {query, strategy}` — semantic or causal retrieve (RelOp + dummy Chroma MiniLM). Never invent Chroma filters.
6. `rr_causal {question}` — CausalPlan (pair / attach / intervene / vs_world). Never invent SQL.
7. `rr_promote` / `rr_analytics_promote` **only** after a sandbox ticket exists.

If the user gives a DSN and a question in one turn, call `rr_question` with both — it boots automatically.

```text
rr_boot      dsn=./superstore.sqlite
rr_question  question="customers in West"
rr_question  composite=west_sales_by_category
rr_promote   ir=<RelOp from question>
```

## Tools

| Tool | Role |
| --- | --- |
| `rr_install` | Cursor / VS Code / Claude / Windsurf / generic install JSON |
| `rr_boot` | Connect any catalogued DSN + dummy sandbox once |
| `rr_question` | Any business question (NL / recipe / composite). Auto-boots |
| `rr_health` | Cache + SLM probe |
| `rr_connect` | DSN attach (no live copy) |
| `rr_build` | Schema + dummy DB (once) |
| `rr_schema` | Cached graph / schema card (PII omitted) |
| `rr_policy` | Capabilities and sensitivity |
| `rr_ask` | NL → algebra → sandbox (also auto-boots) |
| `rr_compile` | RelOp → dialect SQL |
| `rr_validate` | Ground RelOp in primitives |
| `rr_sandbox` | Execute RelOp on dummy DB |
| `rr_promote` | Live push (gated) |
| `rr_engines` | Catalog (compile-all, execute by tier) |
| `rr_slm` | Best local or cloud model |
| `rr_analytics_list` | Recipes + primitives + bound measures/dimensions |
| `rr_analytics_scaffold` | Recipe → RelOp plan (no execute) |
| `rr_analytics_rollout` | Run plan on dummy sandbox |
| `rr_analytics_promote` | Live replay after sandbox validation |
| `rr_analytics_primitives` | Taxonomy of RelOp atoms (24 families, including vector RAG, Socratic intent, and RelOp ideation) plus chain rules |
| `rr_analytics_chain` | Compose primitives / named composites into RelOp |
| `rr_rag` | Semantic/causal RAG: RelOp knn + LangChain/Chroma MiniLM (sandboxOnly) |
| `rr_causal` | CausalPlan: pair / attach / intervene / vs_world (sandboxOnly) |
| `rr_chroma` | Dummy Chroma overlay status or resync |
| `rr_automine` | Mine → RelOp reflect → possible etiology evidence → expand catalog follow-ons → citation-grounded report |
| `rr_report` | Planner → researcher → reporter → validator. Cites RelOp / catalog NCBI / KPI cards only. Local SLM or cloud API if provided |
| `rr_kpi` | Bound domain KPI (dummy then live) |
| `rr_gene` | Write the public NCBI FASTA pineoblastoma sample |

Resources: `revolverelate://instructions`, `revolverelate://install`, `revolverelate://primitives`, `revolverelate://composites`, `revolverelate://engines`, `revolverelate://build`, `revolverelate://graph`, `revolverelate://rag`.

Prompts: `rr_loop`, `rr_any_db`, `rr_any_question`.
