"""CORS HTTP surface shared by the Node BFF, Vite, and Streamlit."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from revolverelate.demo.engine import SuperstoreDemo

_ALLOWED = {"*", "http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8501", "http://localhost:8501"}


def _origin(handler: BaseHTTPRequestHandler) -> str:
    origin = handler.headers.get("Origin") or "*"
    if origin in _ALLOWED or "*" in _ALLOWED:
        return origin if origin != "null" else "*"
    return "http://127.0.0.1:5173"


def make_handler(demo: SuperstoreDemo):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict, *, origin: str | None = None) -> None:
            raw = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", origin or _origin(self))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(204, {})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            try:
                if path in {"/", "/api", "/api/health"}:
                    self._send(200, {"surface": "python", **demo.health()})
                    return
                if path == "/api/catalog":
                    self._send(200, demo.catalog())
                    return
                if path == "/api/chroma":
                    self._send(200, demo.chroma())
                    return
                if path == "/api/schema":
                    self._send(200, demo.schema())
                    return
                if path.startswith("/api/tables/"):
                    name = path.split("/")[-1]
                    limit = int((query.get("limit") or ["200"])[0])
                    self._send(200, demo.table(name, limit=limit))
                    return
                self._send(404, {"error": "not found", "path": path})
            except Exception as exc:
                self._send(400, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            try:
                if path == "/api/ask":
                    self._send(200, demo.ask(str(body.get("question") or "")))
                    return
                if path == "/api/promote":
                    self._send(200, demo.promote(body.get("ir") or {}))
                    return
                if path == "/api/question":
                    self._send(
                        200,
                        demo.question(str(body.get("question") or ""), promote=bool(body.get("promote", True))),
                    )
                    return
                if path == "/api/recipe":
                    binds = dict(body.get("args") or {})
                    for key in ("measure", "dimension", "dimension2", "value", "year", "n", "threshold", "min", "left", "right"):
                        if body.get(key) is not None:
                            binds[key] = body[key]
                    self._send(
                        200,
                        demo.recipe(str(body.get("recipe") or ""), promote=bool(body.get("promote", True)), **binds),
                    )
                    return
                if path == "/api/composite":
                    self._send(
                        200,
                        demo.composite(str(body.get("composite") or ""), promote=bool(body.get("promote", True))),
                    )
                    return
                if path == "/api/boot":
                    self._send(200, demo.boot(refresh=bool(body.get("refresh"))))
                    return
                if path == "/api/rag":
                    self._send(
                        200,
                        demo.rag(
                            str(body.get("query") or body.get("question") or ""),
                            strategy=str(body.get("strategy") or "semantic"),
                            column=str(body.get("column") or "ProductName"),
                            n=int(body.get("n") or 5),
                        ),
                    )
                    return
                if path == "/api/causal":
                    self._send(
                        200,
                        demo.causal(
                            str(body.get("question") or body.get("query") or ""),
                            column=str(body.get("column") or "ProductName"),
                            n=int(body.get("n") or 8),
                            explore=bool(body.get("explore")),
                        ),
                    )
                    return
                if path == "/api/causal_explore":
                    self._send(
                        200,
                        demo.causal_explore(
                            str(body.get("question") or body.get("query") or ""),
                            column=str(body.get("column") or "ProductName"),
                            n=int(body.get("n") or 8),
                        ),
                    )
                    return
                if path == "/api/pearl":
                    self._send(
                        200,
                        demo.pearl(
                            str(body.get("question") or body.get("query") or ""),
                            live=bool(body.get("live", True)),
                        ),
                    )
                    return
                self._send(404, {"error": "not found", "path": path})
            except Exception as exc:
                self._send(409 if "promote" in path or "complete" in str(exc).casefold() else 400, {"error": str(exc)})

        def log_message(self, fmt: str, *args) -> None:
            return

    return Handler


def serve(host: str, port: int, root: str | Path | None = None) -> int:
    demo = SuperstoreDemo(root)
    demo.boot()
    httpd = ThreadingHTTPServer((host, port), make_handler(demo))
    print(f"revolverelate demo http://{host}:{port}  live={demo.live}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        demo.close()
        httpd.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RevolveRelate Superstore demo HTTP")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8788)
    p.add_argument("--root", default=None, help="Directory for superstore.sqlite and .revolverelate/")
    args = p.parse_args(argv)
    return serve(args.host, args.port, args.root)
