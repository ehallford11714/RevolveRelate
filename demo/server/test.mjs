import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..", "..");

function waitFor(proc, needle, ms = 20000) {
  return new Promise((resolveWait, reject) => {
    let buf = "";
    const timer = setTimeout(() => reject(new Error(`timeout waiting for ${needle}`)), ms);
    const onData = (chunk) => {
      buf += String(chunk);
      if (buf.includes(needle)) {
        clearTimeout(timer);
        proc.stdout.off("data", onData);
        resolveWait(buf);
      }
    };
    proc.stdout.on("data", onData);
  });
}

test("node backend serves live Superstore and promotes a question", async (t) => {
  const proc = spawn(process.execPath, [join(here, "server.mjs")], {
    cwd: here,
    env: {
      ...process.env,
      RR_DEMO_PORT: "8799",
      REVOLVERELATE_DEMO_ROOT: join(repo, "demo", "data"),
      PYTHONPATH: join(repo, "python", "src"),
      REVOLVERELATE_SLM: "0",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(() => {
    proc.kill();
  });
  proc.stderr.on("data", () => {});
  await waitFor(proc, "http://127.0.0.1:8799");
  const health = await (await fetch("http://127.0.0.1:8799/api/health")).json();
  assert.equal(health.surface, "node");
  assert.equal(health.complete, true);
  assert.ok(health.entities.includes("Customer"));
  const table = await (await fetch("http://127.0.0.1:8799/api/tables/Customer")).json();
  assert.equal(table.target, "live");
  assert.ok(table.records.some((row) => row.CustomerName === "Claire Gute"));
  const asked = await (
    await fetch("http://127.0.0.1:8799/api/question", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: "customers in West" }),
    })
  ).json();
  assert.equal(asked.sandbox.target, "sandbox");
  assert.equal(asked.live.target, "live");
  const names = asked.live.records.map((row) => row.CustomerName || Object.values(row)[0]);
  assert.ok(names.some((name) => String(name).includes("Darrin") || String(name).includes("Hoffman")));
});
