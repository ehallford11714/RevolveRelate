import { useEffect, useMemo, useState } from "react";
import { api } from "./api.js";

function Grid({ title, badge, columns = [], records = [], empty }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h3>{title}</h3>
        {badge ? <span className={`badge ${badge}`}>{badge}</span> : null}
        <span className="muted">{records.length} rows</span>
      </header>
      {records.length === 0 ? (
        <p className="empty">{empty || "No rows."}</p>
      ) : (
        <div className="grid-wrap">
          <table>
            <thead>
              <tr>
                {columns.map((col) => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {records.map((row, i) => (
                <tr key={i}>
                  {columns.map((col) => (
                    <td key={col}>{formatCell(row[col])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function formatCell(value) {
  if (value == null) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

function Bars({ records, labelKey, valueKey }) {
  const rows = useMemo(() => {
    if (!records?.length) return [];
    const keys = Object.keys(records[0] || {});
    const label = labelKey || keys.find((k) => !/value|sales|profit|n|share|qty|quantity/i.test(k)) || keys[0];
    const value = valueKey || keys.find((k) => /value|sales|profit|n|share/i.test(k)) || keys[1] || keys[0];
    const parsed = records
      .map((row) => ({ label: String(row[label]), value: Number(row[value]) }))
      .filter((row) => Number.isFinite(row.value));
    const max = Math.max(...parsed.map((row) => Math.abs(row.value)), 1);
    return parsed.map((row) => ({ ...row, pct: (Math.abs(row.value) / max) * 100 }));
  }, [records, labelKey, valueKey]);
  if (!rows.length) return null;
  return (
    <div className="bars">
      {rows.map((row) => (
        <div key={row.label} className="bar-row">
          <span>{row.label}</span>
          <i style={{ width: `${row.pct}%` }} className={row.value < 0 ? "neg" : ""} />
          <em>{formatCell(row.value)}</em>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [catalog, setCatalog] = useState({ tables: [], questions: [], recipes: [], composites: [], rag: [], causal: [], pearl: [] });
  const [ragQuery, setRagQuery] = useState("bookcase binders");
  const [ragStrategy, setRagStrategy] = useState("semantic");
  const [schema, setSchema] = useState(null);
  const [table, setTable] = useState("Customer");
  const [browse, setBrowse] = useState(null);
  const [question, setQuestion] = useState("customers in West");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const [h, c, s] = await Promise.all([api.health(), api.catalog(), api.schema()]);
        if (cancel) return;
        setHealth(h);
        setCatalog(c);
        setSchema(s);
      } catch (err) {
        if (!cancel) setError(err.message);
      }
    })();
    return () => {
      cancel = true;
    };
  }, []);

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const data = await api.table(table);
        if (!cancel) setBrowse(data);
      } catch (err) {
        if (!cancel) setError(err.message);
      }
    })();
    return () => {
      cancel = true;
    };
  }, [table]);

  async function run(label, fn) {
    setBusy(label);
    setError("");
    try {
      setResult(await fn());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="app">
      <aside>
        <p className="eyebrow">RevolveRelate</p>
        <h1>Superstore live</h1>
        <p className="lede">
          Node backend over the live SQLite book. Questions compile to RelOp, run on the dummy sandbox, then promote the
          same IR to live.
        </p>
        <div className="meta">
          <div>
            <span>Surface</span>
            <strong>{health?.surface || "…"}</strong>
          </div>
          <div>
            <span>Build</span>
            <strong>{health?.complete ? "complete" : "pending"}</strong>
          </div>
          <div>
            <span>Engine</span>
            <strong>{health?.engine || "sqlite"}</strong>
          </div>
          <div>
            <span>Chroma</span>
            <strong>{health?.chroma?.ok ? `${health.chroma.count} chunks` : health?.chroma?.available ? "empty" : "off"}</strong>
          </div>
        </div>
        <h2>Live tables</h2>
        <ul className="nav">
          {(schema?.tables || catalog.tables.map((name) => ({ name, count: "—" }))).map((item) => (
            <li key={item.name}>
              <button className={table === item.name ? "on" : ""} type="button" onClick={() => setTable(item.name)}>
                {item.name}
                <em>{item.count}</em>
              </button>
            </li>
          ))}
        </ul>
        <h2>Ask</h2>
        <ul className="nav">
          {catalog.questions.map((q) => (
            <li key={q}>
              <button type="button" onClick={() => setQuestion(q)}>
                {q}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main>
        <form
          className="ask"
          onSubmit={(ev) => {
            ev.preventDefault();
            run("question", () => api.question(question, true));
          }}
        >
          <label htmlFor="q">Business question</label>
          <div className="ask-row">
            <input id="q" value={question} onChange={(ev) => setQuestion(ev.target.value)} />
            <button type="submit" disabled={Boolean(busy)}>
              {busy === "question" ? "Running…" : "Ask + promote"}
            </button>
          </div>
        </form>
        {error ? <p className="error">{error}</p> : null}
        <Grid
          title={`Live ${table}`}
          badge="live"
          columns={browse?.columns}
          records={browse?.records}
          empty="Boot the Node backend to load Superstore."
        />
        {result && result.mode !== "rag" && result.mode !== "causal" && result.mode !== "causal_explore" && result.mode !== "pearl" ? (
          <div className="compare">
            <Grid title="Dummy sandbox" badge="sandbox" columns={result.sandbox?.columns} records={result.sandbox?.records} />
            <Grid
              title="Live Superstore"
              badge="live"
              columns={result.live?.columns}
              records={result.live?.records}
              empty="Promote is blocked until this RelOp has a sandbox ticket."
            />
          </div>
        ) : null}
        {result?.live?.records?.length ? <Bars records={result.live.records} /> : null}
        {result?.sandbox?.sql ? (
          <section className="panel sql">
            <header className="panel-head">
              <h3>Compiled SQL</h3>
              <span className="muted">deterministic RelOp compiler — not the model</span>
            </header>
            <pre>{result.sandbox.sql}</pre>
          </section>
        ) : null}
        <section className="panel">
          <header className="panel-head">
            <h3>Recipes on live Superstore</h3>
          </header>
          <div className="chips">
            {catalog.recipes.map((row) => (
              <button
                key={row.id}
                type="button"
                disabled={Boolean(busy)}
                onClick={() => run(row.id, () => api.recipe(row.recipe, row.args, true))}
              >
                {row.title}
              </button>
            ))}
          </div>
        </section>
        <section className="panel">
          <header className="panel-head">
            <h3>Semantic / causal RAG</h3>
            <span className="muted">RelOp overlay + dummy Chroma MiniLM — not promoted</span>
          </header>
          <form
            className="ask"
            onSubmit={(ev) => {
              ev.preventDefault();
              run("rag", () => api.rag(ragQuery, ragStrategy, 5));
            }}
          >
            <div className="ask-row">
              <input value={ragQuery} onChange={(ev) => setRagQuery(ev.target.value)} />
              <select value={ragStrategy} onChange={(ev) => setRagStrategy(ev.target.value)}>
                <option value="semantic">semantic</option>
                <option value="causal">causal</option>
              </select>
              <button type="submit" disabled={Boolean(busy)}>
                {busy === "rag" ? "Retrieving…" : "Retrieve"}
              </button>
              <button
                type="button"
                disabled={Boolean(busy)}
                onClick={() => run("causal", () => api.causal(ragQuery))}
              >
                {busy === "causal" ? "Planning…" : "Causal plan"}
              </button>
              <button
                type="button"
                disabled={Boolean(busy)}
                onClick={() => run("causal_explore", () => api.causalExplore(ragQuery))}
              >
                {busy === "causal_explore" ? "Abducing…" : "Causal explore"}
              </button>
              <button
                type="button"
                disabled={Boolean(busy)}
                onClick={() => run("pearl", () => api.pearl(ragQuery))}
              >
                {busy === "pearl" ? "Identifying…" : "Pearl + live"}
              </button>
            </div>
          </form>
          <div className="chips">
            {(catalog.rag || []).map((row) => (
              <button
                key={`${row.strategy}-${row.query}`}
                type="button"
                onClick={() => {
                  setRagQuery(row.query);
                  setRagStrategy(row.strategy);
                }}
              >
                {row.strategy}: {row.query}
              </button>
            ))}
            {(catalog.causal || []).map((row) => (
              <button
                key={row.question}
                type="button"
                onClick={() => {
                  setRagQuery(row.question);
                  setRagStrategy("causal");
                }}
              >
                plan: {row.question}
              </button>
            ))}
            {(catalog.pearl || []).map((row) => (
              <button
                key={`pearl-${row.question}`}
                type="button"
                onClick={() => {
                  setRagQuery(row.question);
                  setRagStrategy("causal");
                }}
              >
                pearl: {row.question}
              </button>
            ))}
          </div>
        </section>
        {result?.mode === "rag" || result?.mode === "causal" || result?.mode === "causal_explore" ? (
          <div className="compare">
            <Grid
              title={
                result.mode === "causal_explore"
                  ? `Winner ${result.composite || "causal RelOp"}`
                  : result.mode === "causal"
                    ? `Causal RelOp (${result.composite || "plan"})`
                    : "RelOp hash knn (sandbox)"
              }
              badge="sandbox"
              columns={result.sandbox?.columns}
              records={result.sandbox?.records}
            />
            <Grid
              title="Chroma MiniLM"
              badge="sandbox"
              columns={result.chroma?.columns}
              records={result.chroma?.records}
              empty="Install python[chroma] and retrieve once so dummy OverlayChunk syncs to Chroma."
            />
          </div>
        ) : null}
        {result?.mode === "pearl" ? (
          <div className="compare">
            <Grid
              title="do() CASE dummy (West discount → 0)"
              badge="sandbox"
              columns={result.sandbox?.columns}
              records={result.sandbox?.records}
            />
            <Grid
              title="do() CASE live Superstore"
              badge="live"
              columns={result.live?.columns}
              records={result.live?.records}
              empty="Promote is blocked until the CASE RelOp has a sandbox ticket."
            />
          </div>
        ) : null}
        {result?.mode === "pearl" && result.identify ? (
          <section className="panel">
            <header className="panel-head">
              <h3>Pearl backdoor</h3>
              <span className="badge live">{result.identify.criterion}</span>
            </header>
            <p className="muted">
              {result.identify.formula} · Z={((result.identify.adjustment) || []).join(", ")} · dummy ATE{" "}
              {result.sandboxAte?.ate == null ? "—" : Number(result.sandboxAte.ate).toFixed(2)} · live ATE{" "}
              {result.liveAte?.ate == null ? "—" : Number(result.liveAte.ate).toFixed(2)}
            </p>
          </section>
        ) : null}
        {result?.mode === "causal_explore" && (result.candidates || []).length ? (
          <Grid
            title="Abduced candidates (dummy scores)"
            badge="sandbox"
            columns={["composite", "score", "sandboxScore", "bonus", "rowCount", "status"]}
            records={result.candidates}
          />
        ) : null}
        <section className="panel">
          <header className="panel-head">
            <h3>Named composites</h3>
          </header>
          <div className="chips">
            {catalog.composites.map((row) => (
              <button
                key={row.id}
                type="button"
                disabled={Boolean(busy)}
                onClick={() => run(row.id, () => api.composite(row.id, true))}
              >
                {row.title}
              </button>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
