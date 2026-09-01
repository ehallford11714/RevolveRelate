"""Streamlit client for the Node Superstore backend (falls back to the Python demo HTTP)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import streamlit as st

DEFAULTS = [
    os.environ.get("RR_DEMO_API", "").strip(),
    "http://127.0.0.1:8787",
    "http://127.0.0.1:8788",
]


def _get(url: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{url.rstrip('/')}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def first_api() -> str:
    last = ""
    for url in [u for u in DEFAULTS if u]:
        try:
            health = _get(url, "/api/health")
            if health.get("ok") or health.get("complete") is not None:
                return url
        except Exception as exc:
            last = str(exc)
    raise RuntimeError(f"No Superstore demo API on 8787 (Node) or 8788 (Python). Last error: {last}")


st.set_page_config(page_title="RevolveRelate Superstore", layout="wide")
st.title("Superstore demo")
st.caption("Browse the live book through the demo API (Node :8787 or Python :8788). Questions become RelOp, run on the dummy sandbox, then promote to live.")

try:
    api = first_api()
    health = _get(api, "/api/health")
    catalog = _get(api, "/api/catalog")
    schema = _get(api, "/api/schema")
except Exception as exc:
    st.error(str(exc))
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Surface", health.get("surface") or "unknown")
c2.metric("Build", "complete" if health.get("complete") else "pending")
c3.metric("Engine", health.get("engine") or "sqlite")
st.caption(health.get("liveDb") or api)

tables = [t["name"] for t in schema.get("tables") or []] or catalog.get("tables") or []
table = st.sidebar.selectbox("Live table", tables)
if table:
    browsed = _get(api, f"/api/tables/{table}")
    st.subheader(f"Live {table}")
    st.dataframe(browsed.get("records") or [], use_container_width=True)

question = st.text_input("Business question", value="customers in West")
preset = st.sidebar.selectbox("Example questions", [""] + list(catalog.get("questions") or []))
if preset:
    question = preset

recipes = catalog.get("recipes") or []
recipe_label = st.sidebar.selectbox("Recipe", ["—"] + [f"{r['id']} — {r['title']}" for r in recipes])
composites = catalog.get("composites") or []
composite_label = st.sidebar.selectbox("Composite", ["—"] + [f"{c['id']} — {c['title']}" for c in composites])
rag_presets = catalog.get("rag") or []
st.sidebar.markdown("**RAG (dummy Chroma)**")
rag_query = st.sidebar.text_input("Retrieve phrase", value="bookcase binders")
rag_strategy = st.sidebar.selectbox("Chunk strategy", ["semantic", "causal"])
if rag_presets:
    pick = st.sidebar.selectbox("RAG presets", ["—"] + [f"{r['strategy']}: {r['query']}" for r in rag_presets])
    if pick != "—":
        rag_strategy, rag_query = pick.split(": ", 1)

col_a, col_b, col_c, col_d, col_e, col_f = st.columns(6)
run_q = col_a.button("Ask + promote", type="primary")
run_r = col_b.button("Run recipe live")
run_c = col_c.button("Run composite live")
run_rag = col_d.button("Retrieve RAG")
run_causal = col_e.button("Causal plan")
run_explore = col_f.button("Causal explore")

result = None
try:
    if run_q and question:
        result = _get(api, "/api/question", {"question": question, "promote": True})
    elif run_r and recipe_label != "—":
        rid = recipe_label.split(" — ", 1)[0]
        row = next(r for r in recipes if r["id"] == rid)
        result = _get(api, "/api/recipe", {"recipe": row["recipe"], "args": row["args"], "promote": True})
    elif run_c and composite_label != "—":
        cid = composite_label.split(" — ", 1)[0]
        result = _get(api, "/api/composite", {"composite": cid, "promote": True})
    elif run_rag and rag_query:
        result = _get(api, "/api/rag", {"query": rag_query, "strategy": rag_strategy, "n": 5})
    elif run_causal and rag_query:
        result = _get(api, "/api/causal", {"question": rag_query})
    elif run_explore and rag_query:
        result = _get(api, "/api/causal_explore", {"question": rag_query})
except urllib.error.HTTPError as exc:
    st.error(json.loads(exc.read().decode("utf-8")).get("error") or str(exc))
except Exception as exc:
    st.error(str(exc))

if result:
    left, right = st.columns(2)
    with left:
        st.subheader("Dummy sandbox")
        st.dataframe((result.get("sandbox") or {}).get("records") or [], use_container_width=True)
        if (result.get("sandbox") or {}).get("sql"):
            st.code(result["sandbox"]["sql"], language="sql")
    with right:
        live_rows = []
        if result.get("mode") in {"rag", "causal", "causal_explore"}:
            title = "Chroma MiniLM" if result.get("mode") == "rag" else f"Causal {result.get('composite') or 'plan'}"
            st.subheader(title)
            st.caption((result.get("backend") or {}).get("via") or result.get("hint") or "dummy overlay")
            if result.get("mode") == "causal_explore":
                st.dataframe(result.get("candidates") or [], use_container_width=True)
            st.dataframe((result.get("chroma") or {}).get("records") or [], use_container_width=True)
        else:
            st.subheader("Live Superstore")
            live_rows = (result.get("live") or {}).get("records") or []
            st.dataframe(live_rows, use_container_width=True)
        if live_rows:
            numeric = [
                k
                for k, v in live_rows[0].items()
                if isinstance(v, (int, float)) and not str(k).endswith("Id")
            ]
            label = next((k for k in live_rows[0] if k not in numeric), None)
            if numeric and label:
                st.bar_chart({row.get(label): row.get(numeric[0]) for row in live_rows})
