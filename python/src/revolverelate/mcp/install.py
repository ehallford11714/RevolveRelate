"""MCP host install snippets. Agents can write these files; humans can copy them."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("REVOLVERELATE_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    if len(here.parents) > 4:
        cand = here.parents[4]
        if (cand / "python" / "src" / "revolverelate").exists():
            return cand
    return Path.cwd()


def python_exe() -> str:
    return sys.executable or "python"


def server_block(*, use_vars: bool = True) -> dict:
    root = str(repo_root())
    src = str(repo_root() / "python" / "src")
    py = python_exe()
    cwd = "${workspaceFolder}/python" if use_vars else str(repo_root() / "python")
    pythonpath = "${workspaceFolder}/python/src" if use_vars else src
    return {
        "command": py,
        "args": ["-m", "revolverelate.mcp"],
        "cwd": cwd,
        "env": {
            "PYTHONUTF8": "1",
            "PYTHONPATH": pythonpath,
        },
    }


def host_configs() -> dict:
    """Configs for Cursor, Claude Desktop, VS Code, Claude Code, Windsurf, generic."""
    cursor_block = server_block(use_vars=True)
    abs_block = server_block(use_vars=False)
    return {
        "pip": "pip install -e python",
        "module": "python -m revolverelate.mcp",
        "script": "revolverelate-mcp",
        "hosts": {
            "cursor": {
                "path": ".cursor/mcp.json",
                "config": {"mcpServers": {"revolverelate": cursor_block}},
            },
            "vscode": {
                "path": ".vscode/mcp.json",
                "config": {
                    "servers": {
                        "revolverelate": {"type": "stdio", **cursor_block},
                    }
                },
            },
            "claude_desktop": {
                "path": "claude_desktop_config.json (app support / Claude)",
                "config": {"mcpServers": {"revolverelate": abs_block}},
            },
            "claude_code": {
                "path": ".mcp.json",
                "config": {"mcpServers": {"revolverelate": cursor_block}},
            },
            "windsurf": {
                "path": ".windsurf/mcp.json",
                "config": {"mcpServers": {"revolverelate": cursor_block}},
            },
            "generic": {
                "path": "mcp.json",
                "config": {"mcpServers": {"revolverelate": cursor_block}},
            },
        },
        "dsnExamples": [
            "./superstore.sqlite",
            "postgresql://user@localhost:5432/app",
            "mysql://user@localhost/app",
            "sqlite:///C:/data/app.db",
            "snowflake://user@account/db/schema",
            "bigquery://project/dataset",
        ],
        "hint": "Install the package once, drop the host config, then call rr_boot with any catalogued DSN and rr_question with any business question. Never invent SQL.",
    }
