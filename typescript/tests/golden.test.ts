import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { compileIr } from "../src/compiler.ts";
import { specDir } from "../src/catalog.ts";

const fixtures = join(specDir(), "fixtures");
const dialects = ["postgres", "sqlite", "duckdb", "mysql", "tds", "snowflake", "bigquery"];

for (const file of readdirSync(fixtures).filter((f) => f.endsWith(".json"))) {
  const fixture = JSON.parse(readFileSync(join(fixtures, file), "utf8"));
  for (const dialect of dialects) {
    test(`${fixture.name} ${dialect}`, () => {
      const { sql, params } = compileIr(fixture.ir, dialect);
      assert.equal(sql, fixture.sql[dialect]);
      assert.deepEqual(params, fixture.params[dialect]);
    });
  }
}
