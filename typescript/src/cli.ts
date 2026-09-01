#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { compileIr } from "./compiler.ts";
import { ENGINES } from "./catalog.ts";

const [cmd, ...rest] = process.argv.slice(2);

if (cmd === "engines") {
  console.log(`${ENGINES.length} engines`);
  for (const eng of ENGINES) {
    console.log(`${eng.id.padEnd(20)} ${eng.emitFamily.padEnd(12)} tier=${eng.executeTier}`);
  }
  process.exit(0);
}

if (cmd === "sql") {
  const irPath = rest[0];
  const engine = rest.includes("--engine") ? rest[rest.indexOf("--engine") + 1] : "sqlite";
  const ir = JSON.parse(readFileSync(irPath, "utf8"));
  const { sql, params } = compileIr(ir.ir || ir, engine);
  console.log(sql);
  console.log(JSON.stringify(params));
  process.exit(0);
}

console.log("revolverelate <engines|sql> [file] [--engine sqlite]");
process.exit(cmd ? 1 : 0);
