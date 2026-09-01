#!/usr/bin/env node
/**
 * Node Superstore backend.
 * Public HTTP for Vite + Streamlit. Live table browse and RelOp ask/promote
 * go through a Python worker so the SLM never invents SQL and promote stays gated.
 */
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..", "..");
const root = process.env.REVOLVERELATE_DEMO_ROOT || join(repo, "demo", "data");
const host = process.env.RR_DEMO_HOST || "127.0.0.1";
const port = Number(process.env.RR_DEMO_PORT || 8787);
const python = process.env.PYTHON || process.env.REVOLVERELATE_PYTHON || "python";
const src = join(repo, "python", "src");

function cors(req, res) {
  const origin = req.headers.origin || "*";
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

function send(res, code, payload) {
  const raw = Buffer.from(JSON.stringify(payload, (_k, v) => (v === undefined ? null : v)));
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": raw.length,
  });
  res.end(raw);
}

class Worker {
  constructor() {
    this.seq = 0;
    this.buf = "";
    this.pending = new Map();
    this.ready = null;
    this.proc = spawn(python, ["-m", "revolverelate.demo.worker", "--root", root], {
      cwd: join(repo, "python"),
      env: {
        ...process.env,
        PYTHONUTF8: "1",
        PYTHONPATH: src,
        REVOLVERELATE_SLM: process.env.REVOLVERELATE_SLM || "0",
        REVOLVERELATE_DEMO_ROOT: root,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.proc.stderr.on("data", (chunk) => {
      const text = String(chunk);
      if (text.trim()) process.stderr.write(`[worker] ${text}`);
    });
    this.proc.stdout.setEncoding("utf8");
    this.proc.stdout.on("data", (chunk) => this._onData(chunk));
    this.proc.on("exit", (code) => {
      const err = new Error(`Superstore worker exited (${code})`);
      for (const [, job] of this.pending) job.reject(err);
      this.pending.clear();
    });
  }

  _onData(chunk) {
    this.buf += chunk;
    let idx;
    while ((idx = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, idx).trim();
      this.buf = this.buf.slice(idx + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        continue;
      }
      if (msg.ready && !this.pending.has(msg.id)) {
        this.ready = msg;
        continue;
      }
      const job = this.pending.get(msg.id);
      if (!job) continue;
      this.pending.delete(msg.id);
      if (msg.ok === false) job.reject(new Error(msg.error || "worker error"));
      else job.resolve(msg.result);
    }
  }

  call(op, payload = {}) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.proc.stdin.write(`${JSON.stringify({ id, op, ...payload })}\n`);
    });
  }
}

const worker = new Worker();

async function route(req, res, url, body) {
  const path = url.pathname.replace(/\/$/, "") || "/";
  if (path === "/" || path === "/api" || path === "/api/health") {
    const health = await worker.call("health");
    return send(res, 200, { surface: "node", port, root, ...health });
  }
  if (path === "/api/catalog") return send(res, 200, await worker.call("catalog"));
  if (path === "/api/chroma") return send(res, 200, await worker.call("chroma"));
  if (path === "/api/schema") return send(res, 200, await worker.call("schema"));
  if (path.startsWith("/api/tables/")) {
    const name = path.split("/").pop();
    const limit = Number(url.searchParams.get("limit") || 200);
    return send(res, 200, await worker.call("table", { name, limit }));
  }
  if (req.method !== "POST") return send(res, 404, { error: "not found", path });
  if (path === "/api/ask") return send(res, 200, await worker.call("ask", body));
  if (path === "/api/promote") return send(res, 200, await worker.call("promote", body));
  if (path === "/api/question") return send(res, 200, await worker.call("question", body));
  if (path === "/api/recipe") return send(res, 200, await worker.call("recipe", body));
  if (path === "/api/composite") return send(res, 200, await worker.call("composite", body));
  if (path === "/api/boot") return send(res, 200, await worker.call("boot", body));
  if (path === "/api/rag") return send(res, 200, await worker.call("rag", body));
  if (path === "/api/causal") return send(res, 200, await worker.call("causal", body));
  if (path === "/api/causal_explore") return send(res, 200, await worker.call("causal_explore", body));
  return send(res, 404, { error: "not found", path });
}

const server = createServer(async (req, res) => {
  cors(req, res);
  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }
  const url = new URL(req.url || "/", `http://${host}:${port}`);
  let body = {};
  if (req.method === "POST") {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString("utf8");
    body = raw ? JSON.parse(raw) : {};
  }
  try {
    await route(req, res, url, body);
  } catch (err) {
    send(res, 409, { error: String(err.message || err) });
  }
});

server.listen(port, host, () => {
  process.stdout.write(`revolverelate node demo http://${host}:${port}  root=${root}\n`);
});
