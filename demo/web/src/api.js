const base = import.meta.env.VITE_API_BASE || "";

async function request(path, options) {
  const res = await fetch(`${base}${path}`, options);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

export const api = {
  health: () => request("/api/health"),
  catalog: () => request("/api/catalog"),
  schema: () => request("/api/schema"),
  table: (name) => request(`/api/tables/${encodeURIComponent(name)}`),
  question: (question, promote = true) =>
    request("/api/question", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, promote }),
    }),
  recipe: (recipe, args = {}, promote = true) =>
    request("/api/recipe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recipe, args, promote }),
    }),
  composite: (composite, promote = true) =>
    request("/api/composite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ composite, promote }),
    }),
  chroma: () => request("/api/chroma"),
  rag: (query, strategy = "semantic", n = 5) =>
    request("/api/rag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, strategy, n }),
    }),
  causal: (question, explore = false) =>
    request("/api/causal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, explore }),
    }),
  causalExplore: (question) =>
    request("/api/causal_explore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
  pearl: (question) =>
    request("/api/pearl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, live: true }),
    }),
};
