# Architecture

```text
live DSN
   │ introspect
   ▼
SchemaGraph + primitives   ──►  .revolverelate/schema.rrgraph.json
   │ dummy rows (masked PII)
   ▼
local sandbox sqlite       ──►  .revolverelate/sandbox.sqlite
   │
   │  NL ──► SLM or linker ──► RelOp IR (spec/relational-ir.schema.json)
   │                              │
   │                         deterministic compiler
   │                              ▼
   │                         dialect SQL + params
   │                              ▼
   │                         execute on sandbox (BEGIN/COMMIT/ROLLBACK)
   │                              ▼
   └── promote (only if build.json status=complete AND sandbox validated)
                              ▼
                           live DSN
```

Invariants:

- The SLM fills RelOp. It never emits SQL.
- Python and TypeScript compilers implement the same `spec/` fixtures.
- Critical/pii columns are masked in the dummy duplicate and omitted from SLM schema cards.
- Live mutate still requires `allow_live` plus `mutate_live` capability.
