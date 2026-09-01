"""Live Superstore walkthrough: connect → build dummy → algebra → SQL → promote."""

from __future__ import annotations

import json
from pathlib import Path

from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import example_questions, write_superstore


def run_superstore_example(workdir: str | Path, *, live_path: str | Path | None = None) -> dict:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    live = Path(live_path) if live_path else workdir / "superstore.sqlite"
    write_superstore(live)
    rr = RevolveRelate.connect(str(live), workdir=workdir)
    build = rr.build(refresh=True, rows_per_entity=6)
    live_emails = rr.adapter.fetchall("SELECT Email FROM Customer ORDER BY CustomerId")
    dummy_emails = rr.sandbox.execute('SELECT Email FROM "Customer" ORDER BY "CustomerId"')[1]
    steps = []
    for question in example_questions():
        asked = rr.ask(question)
        steps.append(
            {
                "question": question,
                "ir": asked["ir"],
                "sql": asked["sql"],
                "params": asked["params"],
                "sandbox_rows": asked["rows"],
                "sandbox_columns": asked["columns"],
                "target": asked["target"],
            }
        )
    promoted = rr.promote(steps[0]["ir"])
    report = {
        "live_db": str(live),
        "build": build,
        "entities": [e.name for e in rr.schema.all_entities()],
        "relationships": [r.name for r in rr.schema.relationships],
        "live_email_sample": live_emails[0][0] if live_emails else None,
        "dummy_email_sample": dummy_emails[0][0] if dummy_emails else None,
        "steps": steps,
        "promote": {
            "target": promoted["target"],
            "columns": promoted["columns"],
            "rows": promoted["rows"],
            "sql": promoted["sql"],
        },
    }
    rr.close()
    out = workdir / "superstore-example.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def print_report(report: dict) -> None:
    print("== Superstore live example ==")
    print(f"live db: {report['live_db']}")
    print(f"build:   {report['build'].get('status')}  entities={report['entities']}")
    print(f"live email:  {report['live_email_sample']}")
    print(f"dummy email: {report['dummy_email_sample']}")
    print()
    for step in report["steps"]:
        print(f"-- {step['question']}")
        print(f"   RelOp: {step['ir']['op']['op']}")
        print(f"   SQL:   {step['sql']}")
        print(f"   params:{step['params']}")
        print(f"   sandbox rows ({step['target']}): {len(step['sandbox_rows'])}")
        print()
    promo = report["promote"]
    print(f"-- promote first question to live ({promo['target']})")
    print(f"   SQL: {promo['sql']}")
    print(f"   live rows: {len(promo['rows'])}")
