"""Tutorial runner: Superstore facts + overlay RAG (automine covered in test_research)."""

from __future__ import annotations

from revolverelate.samples.tutorial import run_tutorial
from revolverelate.vector.overlay import OVERLAY


def test_tutorial_superstore_and_rag(tmp_path):
    report = run_tutorial(tmp_path, parts=("superstore", "rag"))
    store = report["superstore"]
    assert store["build"] == "complete"
    assert "Customer" in store["entities"]
    assert OVERLAY not in store["entities"]
    assert store["overlayChunks"] > 0
    assert {"semantic", "causal"} <= set(store["chunkStrategies"])
    assert store["ask"]["sandboxRows"] >= 1
    assert store["ask"]["liveRows"] >= 1
    rag = report["rag"]
    assert "semantic" in rag["strategies"]
    assert rag["semantic"]["query"] == "bookcase binders"
    assert rag["causal"]["query"].startswith("sales fell")
    assert rag["chunkDemos"]["causal"]
    assert any(row.get("cue") == "because" for row in rag["chunkDemos"]["causal"])
    assert (tmp_path / "tutorial.json").exists()
    assert "automine" not in report
