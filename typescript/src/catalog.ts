import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export type Engine = {
  id: string;
  family: string;
  aliases: string[];
  schemes: string[];
  quoting: string;
  limitStyle: string;
  introspect: string;
  readonlySession: string[];
  optimizer: string[];
  description: string;
  emitFamily: string;
  placeholder: string;
  executeTier: string;
  connectionFamily: string;
};

export function specDir(): string {
  if (process.env.REVOLVERELATE_SPEC) return process.env.REVOLVERELATE_SPEC;
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "..", "..", "spec");
}

const raw = JSON.parse(readFileSync(join(specDir(), "engines.json"), "utf8")) as {
  engines: Engine[];
};

export const ENGINES: Engine[] = raw.engines;

const by: Record<string, Engine> = {};
for (const eng of ENGINES) {
  by[eng.id] = eng;
  for (const alias of [...eng.aliases, ...eng.schemes]) {
    if (!by[alias]) by[alias] = eng;
  }
}

export function getEngine(name: string): Engine {
  const key = name.trim().toLowerCase().replace(/-/g, "_").replace(/ /g, "_");
  for (const eng of ENGINES) {
    if (eng.emitFamily === key) return eng;
  }
  const found = by[key];
  if (!found) {
    throw new Error(`Unknown engine ${name}. Catalog has ${ENGINES.length} engines`);
  }
  return found;
}

export function quoteIdent(engine: Engine, name: string): string {
  if (name === "*") return "*";
  let quoting = engine.quoting;
  if (engine.emitFamily === "bigquery") quoting = "backtick";
  if (quoting === "backtick") return `\`${name.replace(/`/g, "``")}\``;
  if (quoting === "bracket") return `[${name.replace(/]/g, "]]")}]`;
  if (quoting === "none") return name;
  return `"${name.replace(/"/g, '""')}"`;
}

export function placeholder(engine: Engine, index: number): string {
  let style = engine.placeholder;
  if (engine.emitFamily === "bigquery") style = "at";
  if (style === "dollar") return `$${index}`;
  if (style === "at") return `@p${index}`;
  if (style === "colon") return `:p${index}`;
  return "?";
}
