"""Live Superstore demo engine + HTTP used by the Node / Vite / Streamlit clients."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from revolverelate.demo.engine import SuperstoreDemo
from revolverelate.demo.http import make_handler


def test_live_superstore_browse_ask_promote(tmp_path):
    demo = SuperstoreDemo(tmp_path)
    health = demo.boot()
    assert health["complete"] is True
    assert set(health["entities"]) == {"Customer", "Product", "Orders", "OrderLine"}
    customers = demo.table("Customer")
    assert customers["target"] == "live"
    names = {row["CustomerName"] for row in customers["records"]}
    assert "Claire Gute" in names
    emails = {row["Email"] for row in customers["records"]}
    assert "claire.gute@example.com" in emails

    asked = demo.question("customers in West", promote=True)
    assert asked["sandbox"]["target"] == "sandbox"
    assert asked["live"]["target"] == "live"
    live_names = {row.get("CustomerName") for row in asked["live"]["records"]}
    assert live_names & {"Darrin Van Huff", "Brosina Hoffman", "Irene Maddox"}
    assert "Claire Gute" not in live_names
    dummy_blob = json.dumps(asked["sandbox"]["records"])
    assert "mask_" in dummy_blob or "claire.gute@example.com" not in dummy_blob

    recipe = demo.recipe("sum_by_dimension", measure="Sales", dimension="Region")
    assert recipe["promoted"] is True
    by_region = {row.get("Region"): row.get("value") or row.get("Sales") for row in recipe["live"]["records"]}
    assert by_region.get("West") == 4583 or abs(float(by_region.get("West") or 0) - 4583) < 1
    demo.close()


def test_demo_http_matches_node_contract(tmp_path):
    demo = SuperstoreDemo(tmp_path)
    demo.boot()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(demo))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    try:
        conn = HTTPConnection(host, port, timeout=30)
        conn.request("GET", "/api/health")
        health = json.loads(conn.getresponse().read())
        assert health["surface"] == "python"
        assert health["complete"] is True
        conn.request("GET", "/api/tables/Customer")
        table = json.loads(conn.getresponse().read())
        assert table["target"] == "live"
        assert any(row["CustomerName"] == "Claire Gute" for row in table["records"])
        conn.request(
            "POST",
            "/api/question",
            body=json.dumps({"question": "products in Technology"}),
            headers={"Content-Type": "application/json"},
        )
        asked = json.loads(conn.getresponse().read())
        assert asked["sandbox"]["sql"].startswith("SELECT")
        assert asked["live"]["target"] == "live"
        conn.close()
    finally:
        httpd.shutdown()
        demo.close()
