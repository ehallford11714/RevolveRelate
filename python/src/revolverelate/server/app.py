"""Minimal agent HTTP surface. /ask is sandbox-only; /promote requires a complete build cache."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from revolverelate.revolverelate import RevolveRelate


def serve(host: str, port: int, dsn: str, workdir: str | Path) -> int:
    rr = RevolveRelate.connect(dsn, workdir=workdir)
    if not rr.cache.is_complete():
        try:
            rr.build()
        except Exception:
            pass

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/policy":
                try:
                    self._json(200, rr.policy)
                except Exception as exc:
                    self._json(409, {"error": str(exc)})
                return
            if self.path == "/plan" or self.path == "/health":
                self._json(200, {"build": rr.cache.load(), "complete": rr.cache.is_complete()})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/ask":
                try:
                    self._json(200, rr.ask(body.get("question") or ""))
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                return
            if self.path == "/sandbox":
                try:
                    ir = body.get("ir") or rr.ask(body.get("question") or "")["ir"]
                    sql, params, columns, rows = rr.sandbox.run_ir(ir)
                    self._json(200, {"sql": sql, "params": params, "columns": columns, "rows": rows})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                return
            if self.path == "/promote":
                try:
                    self._json(200, rr.promote(body["ir"], allow_live=bool(body.get("allow_live"))))
                except Exception as exc:
                    self._json(409, {"error": str(exc)})
                return
            self._json(404, {"error": "not found"})

        def log_message(self, fmt: str, *args) -> None:
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()
    return 0
