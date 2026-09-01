"""MCP bind: install any host, boot any DSN, ask any question."""

from __future__ import annotations

from revolverelate.mcp.server import MCP_TOOLS, dispatch, list_prompts, list_resources, read_resource
from revolverelate.samples.superstore import write_superstore


def test_rr_install_lists_hosts():
    payload = dispatch("rr_install", {})
    hosts = payload["hosts"]
    assert set(hosts) >= {"cursor", "vscode", "claude_desktop", "claude_code", "windsurf", "generic"}
    assert "revolverelate.mcp" in payload["module"]
    cursor = hosts["cursor"]["config"]["mcpServers"]["revolverelate"]
    assert cursor["args"] == ["-m", "revolverelate.mcp"]
    assert payload["dsnExamples"]


def test_rr_boot_and_question_auto_build(tmp_path, monkeypatch):
    live = write_superstore(tmp_path / "superstore.sqlite")
    monkeypatch.chdir(tmp_path)
    args = {"dsn": str(live), "workdir": str(tmp_path), "rows": 4}

    health = dispatch("rr_health", {"workdir": str(tmp_path)})
    assert health["complete"] is False
    assert health["next"] == "rr_boot"

    asked = dispatch("rr_question", {**args, "question": "customers in West"})
    assert asked.get("error") is None, asked
    assert asked["mode"] == "ask"
    assert asked["target"] == "sandbox"
    assert asked["validated"] is True
    assert asked["sql"].startswith("SELECT")
    assert asked["ir"]["kind"] == "query"
    assert asked["rows"]

    booted = dispatch("rr_boot", args)
    assert booted["ok"] is True
    assert booted["complete"] is True
    assert "Sales" in booted["measures"]
    assert "Region" in booted["dimensions"]
    assert "west_sales_by_category" in booted["composites"]
    assert "sum_by_dimension" in booted["recipes"]


def test_rr_question_composite_and_recipe(tmp_path, monkeypatch):
    live = write_superstore(tmp_path / "superstore.sqlite")
    monkeypatch.chdir(tmp_path)
    args = {"dsn": str(live), "workdir": str(tmp_path), "rows": 4}

    chained = dispatch("rr_question", {**args, "composite": "west_sales_by_category"})
    assert chained.get("error") is None, chained
    assert chained["mode"] == "chain"
    assert chained["status"] == "sandbox_ok"
    assert chained["sql"]
    assert chained["rowCount"] is not None

    recipe = dispatch(
        "rr_question",
        {**args, "recipe": "sum_by_dimension", "measure": "Sales", "dimension": "Region"},
    )
    assert recipe.get("error") is None, recipe
    assert recipe["mode"] == "recipe"
    assert recipe["status"] == "sandbox_ok"


def test_mcp_resources_and_prompts():
    uris = {r["uri"].split("?")[0] for r in list_resources()}
    assert uris >= {
        "revolverelate://instructions",
        "revolverelate://install",
        "revolverelate://primitives",
        "revolverelate://composites",
        "revolverelate://engines",
    }
    names = {p["name"] for p in list_prompts()}
    assert names >= {"rr_loop", "rr_any_db", "rr_any_question"}
    assert {t["name"] for t in MCP_TOOLS} >= {"rr_automine", "rr_report", "rr_kpi", "rr_gene"}
    install = read_resource("revolverelate://install")
    text = install["contents"][0]["text"]
    assert "cursor" in text
    engines = read_resource("revolverelate://engines")
    assert '"count"' in engines["contents"][0]["text"]
