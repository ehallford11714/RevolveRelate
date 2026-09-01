"""JSONL worker the Node Superstore backend talks to. One process, one live DB."""

from __future__ import annotations

import argparse
import json
import sys

from revolverelate.demo.engine import SuperstoreDemo


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RevolveRelate Superstore JSONL worker")
    p.add_argument("--root", default=None)
    args = p.parse_args(argv)
    demo = SuperstoreDemo(args.root)
    demo.boot()
    print(json.dumps({"ok": True, "ready": True, **demo.health()}, default=str), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            op = str(msg.get("op") or "health")
            result = demo.dispatch(op, msg)
            print(json.dumps({"id": msg.get("id"), "ok": True, "result": result}, default=str), flush=True)
        except Exception as exc:
            print(json.dumps({"id": (msg.get("id") if isinstance(msg, dict) else None), "ok": False, "error": str(exc)}), flush=True)
    demo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
