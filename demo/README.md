# Superstore demos

Live Tableau-style Superstore (Customer, Product, Orders, OrderLine) behind a **Node HTTP backend**. Vite React and Streamlit only talk to that API. Questions become RelOp, run on the dummy sandbox, then promote the same IR to live.

```text
Vite :5173          ─┐
Streamlit :8501     ─┼─► Node :8787 ─► Python worker ─► superstore.sqlite (live)
                     │                      └──────────► .revolverelate/sandbox.sqlite (dummy)
                     └─ fallback Python HTTP :8788 (same /api contract)
```

## Start

```powershell
pip install -e python
pip install -e "python[demo]"          # Streamlit only
pip install -e "python[chroma]"        # LangChain + Chroma MiniLM RAG
python -m revolverelate demo --port 8788 --root demo/data

# Node backend (public API the UIs expect)
node demo/server/server.mjs
# http://127.0.0.1:8787/api/health

# Vite
cd demo/web
npm install
npm run dev
# http://127.0.0.1:5173

# Streamlit
$env:RR_DEMO_API = "http://127.0.0.1:8787"
streamlit run demo/streamlit/app.py
```

Node is not required for the Python API or Streamlit. Streamlit tries `:8787` (Node) then `:8788` (Python).

## API

| Method | Path | Role |
| --- | --- | --- |
| GET | `/api/health` | Build cache + live path |
| GET | `/api/catalog` | Tables, questions, recipes, composites |
| GET | `/api/schema` | Live columns + row counts |
| GET | `/api/tables/{Customer\|Product\|Orders\|OrderLine}` | Live browse |
| POST | `/api/question` | `{question, promote?}` sandbox then live |
| POST | `/api/recipe` | `{recipe, args, promote?}` |
| POST | `/api/composite` | `{composite, promote?}` |
| POST | `/api/promote` | `{ir}` after a sandbox ticket |
| GET | `/api/chroma` | Dummy Chroma overlay status |
| POST | `/api/rag` | `{query, strategy, n}` RelOp + MiniLM retrieve (not promoted) |
| POST | `/api/causal` | `{question, explore?}` CausalPlan → pair / vs_world (not promoted) |
| POST | `/api/causal_explore` | `{question}` rank legal causal RelOps on dummy, keep winner (not promoted) |

Browse is live Superstore (real emails). Ask always hits the dummy duplicate first. Promote replays the validated RelOp on live.
