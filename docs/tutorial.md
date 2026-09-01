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

### Gate, memory, reuse

Every pass now carries a **gate verdict**, borrowed from the autocausal bridge in kineteq-ai-v2:

| Verdict | Meaning |
| --- | --- |
| `supported` | live cue pairs named at least one catalogued candidate — still heuristic, identification still `none` |
| `review_required` | live pairs exist but no catalogued candidate appears in them |
| `refused` | no bound text column or no live pairs; nothing is paired, so nothing is invented |
| `failed` | the RelOp raised |

Evidence rows are also **remembered**: each becomes semantic + causal chunks in the dummy `OverlayChunk` under the virtual entity `AutomineEvidence`, so the next pass (and you) can knn-recall them with the same RelOp used for live text:

```powershell
python -m revolverelate recall --dsn .\gene.sqlite --query "DICER1 causes" --n 3
```

A finished run (`stop=goalReached`) is **reused** when the same domain + question comes back (`reuseKey`); pass `--rerun` to mine again.

---

## 3b. Finance — equities price moves as possible drivers

Same loop, different domain. `spec/domain-finance.json` describes a small public universe (AAPL, MSFT, NVDA, JPM, XOM) plus catalogued sector peers. The writer pulls daily bars from **yfinance** when it is installed (`pip install -e "python[finance]"`); otherwise it bakes a seeded series and stamps `Source=baked` on every ticker, so the tutorial and tests run offline.

```powershell
python -m revolverelate finance --dest .\equities.sqlite            # add --offline to skip yfinance
python -m revolverelate automine --dsn .\equities.sqlite --question "what causes AAPL price moves" --passes 3
python -m revolverelate report --markdown
```

What the writer computes from the bars — nothing else:

| Column | Meaning |
| --- | --- |
| `PriceMove.ZScore` | daily return z-score over a 20-bar window; a bar is a move when it passes `zThreshold` (2.0) |
| `VolumeRatio` | volume over the 20-bar average |
| `GapPct` | open vs prior close |
| `Regime` | `bull` / `bear` from the 50-bar mean |
| `Note` | a **templated** sentence: "AAPL fell 6.1% on … because volume ran 2.4x its 20-day average; therefore this was a high-volume down move in a bear regime." |
| `MarketEvent` | yfinance earnings dates when available, otherwise catalogued quarterly stand-ins |

The `because` / `therefore` in a `Note` are discourse cues so `chunk_causal` can pair the two halves. They restate measured facts; they are not a causal claim, and the report says so.

Automine binds `AbsReturn` / `Symbol` / `Note` with a slice on `AAPL`, pairs every move note, and emits one evidence row per **ticker × driver** (`volume spike`, `opening gap`, `trend regime`, `earnings reaction`). Rows for the sliced ticker come first. Follow-ons are the peers each ticker names in its live `Peers` text — GOOGL, AMD, META, BAC, CVX — loaded only because `spec/domain-finance.json` catalogues them.

What we took from kineteq-ai-v2's Quant Finance Lab: yfinance history, technical regime, sector cross-analysis, the gate verdicts, and reuse-by-task-key. What we did not take: its Causal News panel asks an LLM to find news and assign "Bayesian posteriors". No news is invented here and no model writes SQL. This is not a forecast and not investment advice.

```text
rr_finance   offline=true
rr_boot      dsn=./equities.sqlite
rr_question  question="what causes AAPL price moves"
rr_automine  question="what causes AAPL price moves" passes=3
rr_recall    query="AAPL fell because volume ran"
rr_kpi       kpi=abs_move_by_symbol
rr_report
```

---

## 4. Autonomy loop — let the engine search atom chains

Everything above picks from named recipes. The autonomy loop (`spec/autonomy.json`) works one level down, on the atoms in `spec/analytics-primitives.json`.

Give it a goal in English. It binds a measure, a dimension, and an optional slice from the booted schema, then:

1. **seeds** a few chains (`scan_fact → agg_sum_by → sort_value_desc → limit`, a share-of-total, a sliced sum, plus any named composite whose columns exist, plus winners from the last run);
2. **checks** every chain with the composite grammar — an illegal chain never reaches the sandbox;
3. **rolls out** legal chains on the dummy and gets a ticket for each;
4. **scores** them against the goal (ran, row band, goal measure/dimension/slice bound, magnitude, novelty, minus depth);
5. **keeps** the top three and **mutates** them one atom at a time — swap a measure or dimension bind, add a restrict / cut / compare, finish with order + cap, drop an atom, or splice two parents at their collapse;
6. stops on target, patience, or the generation budget, then **replays the winner live** and writes `.revolverelate/autonomy.json`.

```bash
python -m revolverelate autonomy --objective "west sales by category" --dsn ./superstore.sqlite
python -m revolverelate autonomy --objective "cases by gene" --dsn ./gene.sqlite --no-live
```

```python
state = rr.autonomy("west sales by category", generations=4)
state["winner"]["ops"]       # e.g. ['scan_fact', 'eq', 'agg_sum_by', 'sort_value_desc', 'limit']
state["illegalNeverRan"]     # chains the grammar rejected before the sandbox
state["live"]["rowCount"]    # the same RelOp replayed live
```

MCP: `rr_autonomy objective="west sales by category" generations=4`.

The score is a search heuristic. A high score means "this RelOp is legal, ran on the dummy, and is bound to what you asked for" — nothing more. An SLM, if present, may only propose bind names; it cannot add atoms or write SQL.

---

## 5. Self-directed — let the engine form and test its own hypotheses

Give it nothing. With no objective, `autonomy` switches to the hypothesis loop in `spec/hypotheses.json` (also available on its own as `hypothesize`). The engine:

1. **surveys** the booted schema — fact table, measures, low-cardinality dimensions with sample values (keys, names, codes, and dates are never dimensions), a date column and its years, and the detected domain;
2. **forms** hypotheses from five testable shapes, each a statement with a declared threshold:

   | Kind | Statement | Test |
   | --- | --- | --- |
   | concentration | The leading *Category* accounts for at least 40% of total *Sales*. | `win_share_total`, top row's share, at least two groups |
   | contrast | *Sales* for *Category = Furniture* is at least 1.25x the peer mean. | `agg_sum_by → vs_peer`, value / peer |
   | association | Average *Sales* is at least 1.2x higher where *Quantity* is above its median. | `median`, then `measure_above → agg_avg` vs `agg_avg` |
   | correlation | *AbsReturn* and *VolumeRatio* are linearly associated (\|r\| ≥ 0.3). | `corr` pairs → Pearson r, inconclusive under 20 pairs |
   | trend | Total *Sales* in 2017 exceeds 2016 by at least 10%. | `period_year → agg_sum` for each year |

   Extra seeds come from the autominer's catalogued candidates (each becomes a contrast hypothesis) and, if an SLM is configured, from proposals it makes as JSON bound to listed names only;
3. **tests** each one as a RelOp chain — grammar check, dummy rollout for the ticket, then the **verdict from live rows**: `supported`, `refuted`, `inconclusive` (too few rows, absent value, null denominator), `illegal`, or `failed`. With `--no-live` every verdict is graded `dummy_only` and does not count as evidence;
4. **derives** new hypotheses from what it learned — this is where novelty comes from. A supported concentration drills into the winning slice against the other dimensions and sharpens into a contrast on the top value; a supported contrast drills down and also tries to **refute itself** by asking the same contrast for peer values (if a peer passes too, the loop records that the finding was less specific than it looked); supported associations, correlations, and trends are re-asked inside slices;
5. **remembers** every test in `.revolverelate/hypotheses.json` so a later run continues from where it stopped instead of re-testing, and writes supported findings into the evidence overlay so `recall` and the autominer can see them;
6. **searches** — the strongest supported hypothesis becomes the objective of the atom search above, so the search is directed by evidence rather than by a typed goal.

```bash
python -m revolverelate hypothesize --dsn ./superstore.sqlite --brief
python -m revolverelate hypothesize --domain finance --rounds 3 --brief
python -m revolverelate autonomy --self --dsn ./equities.sqlite
```

```python
state = rr.autonomy()                    # no objective → self-directed
state["supported"][0]["statement"]       # e.g. 'AbsReturn and VolumeRatio are linearly associated (|r| >= 0.3).'
state["tested"][8]["origin"]             # e.g. 'derive:refute_peers'
state["peerNotes"]                       # findings a peer also passed
state["search"]["objective"]             # the atom search this run launched
```

MCP: `rr_hypothesize rounds=3` or `rr_autonomy` with no objective.

A verdict is a threshold comparison on live rows, uncorrected for multiple comparisons. "Supported" means the statement held on this data at this threshold — not that one column causes another. Identification stays none.

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
| `spec/autonomy.json` | Atom-level search loop: seeds, mutations, score weights, stop |
| `spec/hypotheses.json` | Self-directed loop: hypothesis kinds, thresholds, follow-up rules, verdicts |
| `spec/deep-research.json` | Report agents and citation allow-list |
| `spec/domain-gene.json` | Accessions, follow-ons, KPIs |
