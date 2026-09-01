# Superstore live example

For the guided path that also covers **RAG chunks** and the **autominer**, start at [tutorial.md](tutorial.md).

This is the working loop on a Tableau-style Superstore database: **Customers**, **Products**, **Orders**, **OrderLine**.

## What happens

1. **Connect** the live Superstore SQLite file in this environment.
2. **Read the schema** (tables, keys, foreign keys, samples).
3. **Build once** — write `.revolverelate/schema.rrgraph.json` and a **local duplicate** filled with dummy rows. Live emails never copy into the sandbox (`mask_Email_*`).
4. **Ask in English** — the best local SLM (or the deterministic linker) fills **relational algebra** (RelOp). A parser compiles that IR to dialect SQL. SQL runs on the dummy DB.
5. **Promote** — the same RelOp can replay on live Superstore **only after** the build cache is complete and the sandbox run was saved.

The model never writes SQL. The compiler is dialect-agnostic (the same RelOp emits postgres, mysql, sqlite, snowflake, …).

## Run it

```powershell
pip install -e python
python -m revolverelate superstore --dest .\superstore.sqlite
python -m revolverelate example --workdir . --dest .\superstore.sqlite
```

Or from Python:

```python
from revolverelate.samples.walkthrough import run_superstore_example, print_report

report = run_superstore_example(".", live_path="superstore.sqlite")
print_report(report)
```

## Example questions

| Question | Algebra | What it proves |
| --- | --- | --- |
| `customers in West` | Scan Customer, filter Region = West | Schema-bound filter → SQL |
| `orders in California` | Scan Orders ⋈ Customer, filter State | Join imputation from FKs |
| `orderlines over 500` | Scan OrderLine, filter Sales > 500 | Numeric predicate + synonym `sales` |
| `products in Technology` | Scan Product, filter Category | Dimension filter |

Each question is executed on the **dummy sandbox first**. The walkthrough then **promotes** `customers in West` to live Superstore, which returns real names (Darrin Van Huff, Brosina Hoffman, …) — not dummy rows.

## Analytics library (scaffold → dummy → live)

Named RelOp recipes bind to Superstore measures (`Sales`, `Profit`, `Quantity`, `Discount`) and dimensions (`Region`, `Segment`, `Category`, `State`, `ShipMode`). Logic always rolls out on the dummy duplicate; the same plan then promotes to live.

```powershell
python -m revolverelate example-analytics --dest .\superstore.sqlite
```

```python
from revolverelate.samples.analytics_superstore import run_superstore_analytics, print_report

report = run_superstore_analytics(".", live_path="superstore.sqlite")
print_report(report)
```

Live numbers below are from this bundled Superstore (10 customers, 12 orders, 16 lines). Dummy-sandbox row counts can differ; that is expected.

| Use case | Recipe | Live result (promoted) |
| --- | --- | --- |
| Where does revenue concentrate? | `sum_by_dimension` Sales × Region | West 4583 · South 1976 · Central 228 |
| Which category carries the book? | `sum_by_dimension` Sales × Category | Furniture 4646 · Technology 1855 · Office Supplies 287 |
| Which segment is profitable? | `sum_by_dimension` Profit × Segment | Corporate +753 · Consumer +41 · Home Office −13 |
| Typical discount? | `avg_measure` Discount | 0.17 |
| Whale lines | `top_n` Sales | 2574, 958, 907, 732 |
| Category mix vs total | `share_of_total` | Furniture 4646 / 6787 total |
| Region × category cube | `multi_group` | West×Furniture 2694, South×Furniture 1951, West×Tech 1855 |
| 2016 book by region | `period_slice` year=2016 | West 3254 · South 994 |
| Segment Pareto | `pareto` Sales × Segment | Consumer 4183 → running 4183; Corporate 2588 → 6772 |

## Composite rules

Chains are ordered atoms. Phase rank must not go backward except at `with_cte` (a second pass). Typical depth is 3–8; **deep** is 12; **hard max is 24**. One collapse (`aggregate` / `stat` / `hierarchy`) per pass.

```powershell
python -m revolverelate analytics chain --composite deep_compare_cut --rollout --dsn .\superstore.sqlite
```

## Primitives (chain any business question)

Atoms live in `spec/analytics-primitives.json` (10 families, 100+ ops). Bind names to the schema at apply time. Example: West sales by category.

```powershell
python -m revolverelate analytics chain --composite west_sales_by_category --rollout --dsn .\superstore.sqlite
```

```python
from revolverelate.analytics.primitives import chain

ir = chain(rr.schema, [
    {"op": "scan_fact"},
    {"op": "eq", "column": "Region", "value": "West"},
    {"op": "agg_sum_by", "measure": "Sales", "dimension": "Category"},
    {"op": "sort_value_desc"},
    {"op": "limit", "n": 10},
])
rr.analytics.scaffold_chain([
    {"op": "scan_fact"},
    {"op": "eq", "column": "Region", "value": "West"},
    {"op": "agg_sum_by", "measure": "Sales", "dimension": "Category"},
    {"op": "sort_value_desc"},
    {"op": "limit", "n": 10},
])
```

Promote is refused until `analytics.rollout` has saved a sandbox ticket.

```python
rr.analytics.scaffold("sum_by_dimension", measure="Sales", dimension="Region")
rr.analytics.rollout("sum-by-dimension-sales-region")
rr.analytics.promote("sum-by-dimension-sales-region")
```

## Agent / MCP

Any MCP host can drive the same Superstore loop (auto-install + auto-boot):

```text
rr_install
rr_boot      dsn=./superstore.sqlite
rr_question  question="customers in West"
rr_question  composite=west_sales_by_category
rr_promote   ir=<RelOp from question>
```

`rr_question` with `dsn` + `question` in one call boots the dummy sandbox automatically.

See [mcp.md](mcp.md). Vite React and Streamlit browse the same live Superstore through the Node backend: [demo/README.md](../demo/README.md).

## Tests

```powershell
python -m pytest python/tests/test_superstore_live.py -q
```
