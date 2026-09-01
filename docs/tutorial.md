# Tutorial: Superstore, RAG chunks, and the autominer

This walkthrough uses two small public samples:

1. **Superstore** — a Tableau-style sales book (customers, products, orders).
2. **Gene / FASTA** — public NCBI protein accessions plus abstracts about pineoblastoma.

You ask in English. The engine fills **relational algebra (RelOp)**. A compiler writes SQL. Work always runs on a **dummy sandbox first**. The same RelOp can then replay on live data. The model never writes SQL, never writes Chroma filters, and never invents citations.

One command runs every part and prints what happened:

```powershell
pip install -e python
python -m revolverelate tutorial --root .\tutorial-run
```

Skip a part with `--skip automine` (or `superstore` / `rag`). JSON lands in `tutorial-run/tutorial.json`.

---

## What you are building toward

| Part | Dataset | What you learn |
| --- | --- | --- |
| 1. Superstore | `superstore.sqlite` | Connect, build once, ask a business question, promote the same plan live |
| 2. RAG / chunks | same Superstore text | `OverlayChunk` splits non-PII text (semantic, causal, topic, …) and retrieve nearest units |
| 3. Autominer | `gene.sqlite` | Loop: ask “what causes …”, collect **possible etiology evidence**, expand catalog follow-ons, draft a cited report |

Honesty for part 3: hits are **heuristic evidence**, identification is **none**, and a `goalReached` stop means “enough evidence rows,” not scientific proof.

---

## 0. Install once

```powershell
pip install -e python
# optional retrieve model (Windows: also set REVOLVERELATE_CHROMA=1 when you want MiniLM)
pip install -e "python[chroma]"
```

Tests keep the language model off (`REVOLVERELATE_SLM=0`). For a local draft on the report, run [Ollama](https://ollama.com) or set `REVOLVERELATE_API_KEY`.

---

## 1. Superstore — facts on a dummy copy, then live

Write the bundled book (10 customers, 8 products, 12 orders, 16 lines):

```powershell
python -m revolverelate superstore --dest .\superstore.sqlite
python -m revolverelate example --dest .\superstore.sqlite
```

`example` does the whole loop: **connect → build → ask → promote**.

### What `build` writes

`.revolverelate/` is the cache. Until `status=complete`, live promote is illegal.

| File | Role |
| --- | --- |
| `schema.rrgraph.json` | Tables, keys, FKs |
| `sandbox.sqlite` | Dummy rows. Live emails become `mask_Email_*` |
| `policy.json` | What the agent may do |
| `build.json` | Complete / incomplete |

Business tables: **Customer**, **Product**, **Orders**, **OrderLine**. `OverlayChunk` is **virtual** — it is not in that list, but the dummy sandbox has it after build.

### Ask in English

```powershell
python -m revolverelate ask "customers in West" --dsn .\superstore.sqlite
python -m revolverelate ask "products in Technology" --dsn .\superstore.sqlite
python -m revolverelate ask "orderlines over 500" --dsn .\superstore.sqlite
```

| Question | What the RelOp does |
| --- | --- |
| `customers in West` | Scan Customer, filter Region |
| `orders in California` | Join Orders to Customer, filter State |
| `orderlines over 500` | Filter Sales |
| `products in Technology` | Filter Category |

The first answer is dummy. Promote the saved RelOp only after that sandbox ticket exists (`python -m revolverelate example` already does this). Live West names look like Darrin Van Huff and Brosina Hoffman — not dummy strings.

### Analytics recipes

```powershell
python -m revolverelate analytics run sum_by_dimension --measure Sales --dimension Region --dsn .\superstore.sqlite
python -m revolverelate analytics chain --composite west_sales_by_category --rollout --dsn .\superstore.sqlite
python -m revolverelate example-analytics --dest .\superstore.sqlite
```

On this sample, live `Sales × Region` concentrates in the West. Recipes bind to columns that exist (`Sales`, `Profit`, `Region`, `Category`, …). More tables and recipes: [examples-superstore.md](examples-superstore.md).

### MCP (any agent host)

```text
rr_boot      dsn=./superstore.sqlite
rr_question  question="customers in West"
rr_question  composite=west_sales_by_category
```

`rr_question` with both `dsn` and `question` boots automatically. See [mcp.md](mcp.md).

---

## 2. RAG and the other semantic chunks

After `build`, every non-PII text column is split into retrieve units and stored on **OverlayChunk** in the dummy sandbox. Live promote rebuilds the **same fields from live text only** (a TEMP table). Emails and passwords are never chunked.

### Strategies (`spec/vector-rag.json`)

| Strategy | What it splits on |
| --- | --- |
| `semantic` | Embedding-distance peaks (topic shift: bookcase vs chairs) |
| `topic` | Next sentence leaves the running centroid |
| `causal` | Cues (`because`, `therefore`, `caused`) tagged cause / effect / condition |
| `discourse` | Contrast (`however`, `although`) |
| `event` | Time chain (`first`, `then`, `after`, `finally`) |
| `sentence` / `window` / `token` / `fixed` | Plain windows |
| `hier` | Parent passage + child sentences |
| `prop` | Clause / proposition |
| `late` | Whole text for embed, sentences for retrieve |
| `recursive` | Paragraph, then sentence, then token |

`build` writes **all** of these into the dummy overlay. RelOp then filters `Strategy`. The retrieve model can swap (hash-16 by default, MiniLM if Chroma is on) without changing the RelOp.

Try the splitters on a note without a database:

```python
from revolverelate.vector.chunk import chunk_text

note = (
    "Demand rose in the West. Sales fell because discounting was heavy. "
    "Therefore inventory piled up. After that, volume recovered."
)
for name in ("semantic", "causal", "topic", "discourse", "event"):
    print(name, chunk_text(note, name))
```

Causal units look like: effect “Sales fell” / cue `because` / cause “discounting was heavy.”

### Retrieve against Superstore

`build` also installs short staging notes (bookcase/binders, chairs/seating, and a West “sales fell because discounting” fact) so retrieve has something to hit on this tiny book.

```powershell
python -m revolverelate rag "bookcase binders" --dsn .\superstore.sqlite --strategy semantic
python -m revolverelate rag "sales fell because discounting" --dsn .\superstore.sqlite --strategy causal
```

Same thing in Python:

```python
from revolverelate.revolverelate import RevolveRelate

rr = RevolveRelate.connect("superstore.sqlite", workdir=".")
rr.build()
print(rr.overlay_stats())   # chunk count, text columns, OverlayChunk fields
print(rr.rag("bookcase binders", strategy="semantic"))
print(rr.rag("sales fell because discounting", strategy="causal"))
rr.close()
```

Or MCP:

```text
rr_boot  dsn=./superstore.sqlite
rr_rag   query="bookcase binders" strategy=semantic
rr_rag   query="sales fell because discounting" strategy=causal
rr_question question="nearest bookcase binders"   # routes to RAG
```

Each call: dummy RelOp ticket (`overlay` → `chunk_*` → `knn`), then the **same RelOp** on live text chunks. You do not write a Chroma `where` filter. Optional MiniLM is physical only (`REVOLVERELATE_CHROMA=1`).

Named composites: `rag_semantic_knn`, `rag_causal_knn`, `rag_topic_knn`, `rag_event_knn`, `rag_causal_pair`.

```powershell
python -m revolverelate analytics chain --composite rag_semantic_knn --rollout --dsn .\superstore.sqlite
```

### Why / what-if (same overlay)

```text
rr_causal  question="sales fell because discounting"
```

That is a CausalPlan of primitive ids (`overlay`, `pair_causal`, …), still not SQL.

---

## 3. Autominer — possible etiologies, then a cited report

Switch corpus. Superstore has product names; the gene sample has abstracts with `because` / `therefore` so causal chunks and RelOp pairs can bind.

```powershell
python -m revolverelate gene --dest .\gene.sqlite
python -m revolverelate automine --dsn .\gene.sqlite --question "what causes pinealblastoma" --passes 3
python -m revolverelate report --markdown
```

`pinealblastoma` is an alias of pineoblastoma.

### What each pass does

1. **Ask / cause** — bind the question to columns that exist (`Abstract`, `Cases`, `Symbol`, …). Dummy causal RelOp, then live overlay.
2. **Evidence** — each live cause/effect pair that mentions a **catalog** gene becomes a possible etiology row. Grade is heuristic. `conclusive` is always false.
3. **Splice / pivot** — fold the cue and gene into the next ask; rotate `Abstract → Evidence → Summary → Header` and `gene → epitope → sirna`.
4. **Expand** — insert only `followOn` symbols from `spec/domain-gene.json` (PLAGL2, CCND2). No invented FASTA.
5. **Rebuild** — refresh the dummy sandbox so the new rows are visible.
6. **Report** — planner → researcher → reporter → validator. Citations are RelOp pairs, NCBI/UniProt accessions, and bound KPI rows only.

Stop when there are enough evidence rows (`goalReached`) or nothing new (`noNewTargets`). That is not a discovery claim.

### What you should see

| Field | Meaning |
| --- | --- |
| `candidates` | DICER1, RB1, DROSHA, DGCR8, and catalog follow-ons if mined |
| `etiologies` | Cue + span + `Disease.Abstract#pk` (or similar) |
| `mined` | PLAGL2 and/or CCND2 from spec, not from model memory |
| `identification` | `none` |
| `.revolverelate/report.md` | Draft with `[E1]`, `[E2]`, … |

KPI on the same schema:

```powershell
# after a gene build
```

```python
from revolverelate.revolverelate import RevolveRelate

rr = RevolveRelate.connect("gene.sqlite", workdir=".")
rr.build()
print(rr.kpi("cases_by_gene", live=True))
rr.close()
```

### MCP

```text
rr_gene
rr_boot      dsn=./gene.sqlite
rr_question  question="what causes pinealblastoma"
rr_automine  question="what causes pinealblastoma" passes=3
rr_report
rr_kpi       kpi=cases_by_gene
```

A local SLM (Ollama) or `REVOLVERELATE_API_KEY` may rewrite report prose. Unknown `[E99]` citations are stripped. Without a model you still get a deterministic draft.

---

## Three rules that do not change

1. **RelOp, not SQL.** The compiler emits dialect SQL. The model does not.
2. **Dummy, then live.** OverlayChunk live is TEMP from live non-PII text.
3. **Evidence, not proof.** Automine + report stay at identification none.

---

## Files to read next

| Path | Role |
| --- | --- |
| [examples-superstore.md](examples-superstore.md) | Superstore questions, recipes, live numbers |
| [mcp.md](mcp.md) | Agent tools |
| [architecture.md](architecture.md) | Cache and promote gate |
| `spec/vector-rag.json` | Chunk strategies and causal cues |
| `spec/automine.json` | Mine loop and stop rules |
| `spec/deep-research.json` | Report agents and citation allow-list |
| `spec/domain-gene.json` | Accessions, follow-ons, KPIs |
