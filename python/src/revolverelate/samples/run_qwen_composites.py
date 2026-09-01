"""Run 100 Superstore composite questions through local qwen3.8:27b."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ["REVOLVERELATE_SLM"] = "auto"
os.environ["REVOLVERELATE_SLM_MODEL"] = "qwen3.8:27b"

from revolverelate.compile.compiler import compile_ir
from revolverelate.ir.nl import question_to_relop
from revolverelate.ir.validate import validate_ir
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.composites import SLM_QUESTIONS
from revolverelate.samples.superstore import write_superstore
from revolverelate.slm.jobs import fill_relop
from revolverelate.slm.probe import probe_slm


def run(workdir: Path) -> dict:
    slm = probe_slm(force=True)
    live = write_superstore(workdir / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=workdir)
    rr.build(refresh=True)
    out = workdir / "qwen27b-composites.json"
    results = []
    for i, question in enumerate(SLM_QUESTIONS, 1):
        started = time.time()
        row = {"n": i, "question": question, "ok": False, "source": None}
        try:
            ir = fill_relop(question, rr.schema, rr.policy, fallback=False)
            row["source"] = "qwen"
            validate_ir(ir, rr.schema)
            sql, params = compile_ir(ir, "sqlite")
            rr.adapter.execute(sql, params)
            row.update({"ok": True, "sql": sql, "params": params, "ir": ir})
        except Exception as slm_exc:  # noqa: BLE001
            row["slm_error"] = str(slm_exc)
            try:
                ir = question_to_relop(question, rr.schema)
                validate_ir(ir, rr.schema)
                sql, params = compile_ir(ir, "sqlite")
                rr.adapter.execute(sql, params)
                row.update(
                    {
                        "ok": True,
                        "source": "linker",
                        "sql": sql,
                        "params": params,
                        "ir": ir,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                row["error"] = str(exc)
        row["seconds"] = round(time.time() - started, 2)
        results.append(row)
        print(
            f"[{i}/100] {row.get('source') or 'FAIL'} {'ok' if row['ok'] else 'FAIL'} {question} ({row['seconds']}s)",
            flush=True,
        )
        summary = {
            "model": slm.to_dict(),
            "passed": sum(1 for r in results if r["ok"]),
            "qwen": sum(1 for r in results if r.get("source") == "qwen"),
            "linker": sum(1 for r in results if r.get("source") == "linker"),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results,
        }
        out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    rr.close()
    print(f"passed {summary['passed']}/100  qwen {summary['qwen']}  linker {summary['linker']}  {out}")
    return summary


if __name__ == "__main__":
    run(Path(os.environ.get("REVOLVERELATE_WORKDIR") or ".").resolve())
