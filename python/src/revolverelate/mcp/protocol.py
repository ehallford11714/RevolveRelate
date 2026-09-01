"""Minimal MCP stdio JSON-RPC — no extra SDK."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, TextIO


def read_message(stdin: TextIO = sys.stdin) -> dict[str, Any] | None:
    header = ""
    while True:
        line = stdin.readline()
        if line == "":
            return None
        if line in ("\n", "\r\n"):
            break
        header += line
        if not header.lower().startswith("content-length:") and line.strip().startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    length = 0
    for hline in header.splitlines():
        if hline.lower().startswith("content-length:"):
            length = int(hline.split(":", 1)[1].strip())
    if length <= 0:
        return None
    body = stdin.read(length)
    if not body:
        return None
    return json.loads(body)


def write_message(msg: dict[str, Any], stdout: TextIO = sys.stdout) -> None:
    data = json.dumps(msg, default=str, ensure_ascii=False)
    raw = data.encode("utf-8")
    stdout.write(f"Content-Length: {len(raw)}\r\n\r\n")
    stdout.flush()
    stdout.buffer.write(raw)
    stdout.buffer.flush()


def mcp_result_text(payload: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}]}


def run_mcp_loop(
    *,
    server_name: str,
    server_version: str,
    instructions: str,
    list_tools: Callable[[], list[dict[str, Any]]],
    call_tool: Callable[[str, dict[str, Any]], Any],
    list_resources: Callable[[], list[dict[str, Any]]] | None = None,
    read_resource: Callable[[str], dict[str, Any]] | None = None,
    list_prompts: Callable[[], list[dict[str, Any]]] | None = None,
    get_prompt: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
    while True:
        msg = read_message(stdin)
        if msg is None:
            return 0
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            caps: dict[str, Any] = {"tools": {"listChanged": False}}
            if list_resources:
                caps["resources"] = {"listChanged": False}
            if list_prompts:
                caps["prompts"] = {"listChanged": False}
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion") or "2024-11-05",
                        "capabilities": caps,
                        "serverInfo": {"name": server_name, "version": server_version},
                        "instructions": instructions,
                    },
                },
                stdout,
            )
            continue
        if method in {"notifications/initialized", "initialized"} or (
            isinstance(method, str) and method.startswith("notifications/")
        ):
            continue
        if method == "ping":
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": {}}, stdout)
            continue
        if method == "tools/list":
            tools = [
                {
                    "name": t["name"],
                    "description": t.get("description") or "",
                    "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
                }
                for t in list_tools()
            ]
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}, stdout)
            continue
        if method == "tools/call":
            name = params.get("name") or ""
            arguments = params.get("arguments") or {}
            try:
                result = call_tool(str(name), dict(arguments))
                err = isinstance(result, dict) and bool(result.get("error") or result.get("isError"))
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {**mcp_result_text(result), **({"isError": True} if err else {})},
                    },
                    stdout,
                )
            except Exception as exc:
                write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {**mcp_result_text({"error": str(exc)}), "isError": True},
                    },
                    stdout,
                )
            continue
        if method == "resources/list" and list_resources:
            write_message(
                {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": list_resources()}},
                stdout,
            )
            continue
        if method == "resources/read" and read_resource:
            write_message(
                {"jsonrpc": "2.0", "id": msg_id, "result": read_resource(str(params.get("uri") or ""))},
                stdout,
            )
            continue
        if method == "prompts/list" and list_prompts:
            write_message(
                {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": list_prompts()}},
                stdout,
            )
            continue
        if method == "prompts/get" and get_prompt:
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": get_prompt(str(params.get("name") or ""), dict(params.get("arguments") or {})),
                },
                stdout,
            )
            continue
        if msg_id is not None:
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                },
                stdout,
            )
    return 0
