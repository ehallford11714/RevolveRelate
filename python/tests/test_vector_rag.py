"""Vector overlay, hash embed, and deeper chunk strategies."""

from __future__ import annotations

from revolverelate.analytics.primitives import apply_primitive, chain, get_composite, list_families
from revolverelate.ir.rel import query
from revolverelate.ir.validate import validate_ir
from revolverelate.revolverelate import RevolveRelate
from revolverelate.samples.superstore import write_superstore
from revolverelate.vector.chunk import STRATEGIES, chunk_text
from revolverelate.vector.embed import cosine, hash_embed, rank_by_cosine
from revolverelate.vector.overlay import OVERLAY


CAUSAL_DOC = (
    "Demand rose in the West. Sales fell because discounting was heavy. "
    "Therefore inventory piled up. The team cut price. After that, volume recovered."
)

SEMANTIC_DOC = (
    "The bookcase holds office binders. Those shelves keep the same binders neat. "
    "Chairs fill the conference room. Seating around the conference table is tight. "
    "Phones and headsets sit on the tech bench. Cables for those phones stay coiled."
)

DISCOURSE_DOC = (
    "West demand looked strong. However discounting erased the margin. "
    "Although volume recovered, profit stayed thin."
)

EVENT_DOC = (
    "First the team cut list price. Then orders accelerated. "
    "After that inventory cleared. Finally the region turned a profit."
)

TOPIC_DOC = SEMANTIC_DOC


def test_hash_embed_is_deterministic_and_normalized():
    a = hash_embed("Hon deluxe chair")
    b = hash_embed("Hon deluxe chair")
    c = hash_embed("Mitel phone")
    assert a == b
    assert abs(sum(v * v for v in a) - 1) < 1e-6
    assert cosine(a, a) > cosine(a, c)


def test_cosine_rank_prefers_shared_tokens():
    ranked = rank_by_cosine("bookcase binders", ["bookcase shelves binders", "conference chairs", "phones"])
    assert "bookcase" in ranked[0][1]


def test_causal_chunks_split_intra_sentence_and_tag_roles():
    rows = chunk_text(CAUSAL_DOC, "causal")
    assert len(rows) >= 5
    cues = {row.get("cue") for row in rows}
    assert {"because", "therefore", "after"} <= cues
    roles = {row.get("role") for row in rows}
    assert {"cause", "effect"} <= roles
    because = [row for row in rows if row.get("cue") == "because"]
    assert any(row.get("role") == "effect" and "sales fell" in row["text"].casefold() for row in because)
    assert any(row.get("role") == "cause" and "discount" in row["text"].casefold() for row in because)


def test_semantic_chunks_break_on_topic_shift():
    rows = chunk_text(SEMANTIC_DOC, "semantic", threshold=0.6)
    assert 2 <= len(rows) <= 6
    texts = " ".join(row["text"].casefold() for row in rows)
    assert "bookcase" in texts and "chair" in texts
    # adjacent furniture vs seating should not all collapse into one unit
    assert not (len(rows) == 1)


def test_topic_chunks_follow_centroid():
    rows = chunk_text(TOPIC_DOC, "topic", threshold=0.25)
    assert 2 <= len(rows) <= 6
    assert all(row.get("level") == "topic" for row in rows)


def test_discourse_chunks_split_contrast():
    rows = chunk_text(DISCOURSE_DOC, "discourse")
    roles = {row.get("role") for row in rows}
    assert "contrast" in roles
    assert "claim" in roles
    cues = {row.get("cue") for row in rows}
    assert cues & {"however", "although"}


def test_event_chunks_form_a_chain():
    rows = chunk_text(EVENT_DOC, "event")
    assert len(rows) >= 3
    assert [row.get("score") for row in rows] == list(range(len(rows)))
    cues = {row.get("cue") for row in rows}
    assert cues & {"first", "then", "after", "finally"}


def test_recursive_and_late_and_hier_parent_links():
    text = "Title\n\nFirst sentence. Second sentence.\n\nNew paragraph here."
    rec = chunk_text(text, "recursive", n=8)
    assert rec
    late = chunk_text(text, "late")
    assert any(row["level"] == "late-doc" for row in late)
    assert any(row["level"] == "late-sent" and row.get("parent") == 0 for row in late)
    hier = chunk_text(text, "hier")
    assert any(row["level"] == "parent" for row in hier)
    assert any(row["level"] == "child" and row.get("parent") == 0 for row in hier)


def test_every_strategy_emits_units():
    for name in STRATEGIES:
        rows = chunk_text(CAUSAL_DOC, name)
        assert rows, name
        assert all(row.get("text") for row in rows), name


def test_overlay_build_and_knn(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build(rows_per_entity=8)
    assert rr.schema.entity(OVERLAY) is not None
    assert {e.name for e in rr.schema.all_entities()} == {"Customer", "Product", "Orders", "OrderLine"}
    families = {f["id"] for f in list_families()}
    assert {"chunk", "vector", "nested"} <= families
    n = rr.sandbox.execute(f'SELECT COUNT(*) FROM "{OVERLAY}"')[1][0][0]
    assert n > 0
    strategies = {r[0] for r in rr.sandbox.execute(f'SELECT DISTINCT Strategy FROM "{OVERLAY}"')[1]}
    assert {"semantic", "causal", "topic", "discourse", "event"} <= strategies
    roles = rr.sandbox.execute(
        f'SELECT COUNT(*) FROM "{OVERLAY}" WHERE Strategy = \'hier\' AND ParentId IS NOT NULL'
    )[1][0][0]
    assert roles > 0
    ir = query(apply_primitive(rr.schema, "knn", None, {"query": "bookcase", "n": 5, "column": "ProductName"}))
    validate_ir(ir, rr.schema)
    ran = rr.execute_ir(ir)
    assert ran["target"] == "sandbox"
    assert ran["sql"]
    assert ran["columns"]
    rr.close()


def test_rag_composites_sandbox_only(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    for cid in ("rag_semantic_knn", "rag_causal_knn", "rag_sim_join", "rag_topic_knn", "rag_event_knn", "rag_causal_pair"):
        spec = get_composite(cid)
        assert spec.get("sandboxOnly") is not True
        plan = rr.analytics.run_chain(composite=cid)
        assert plan["status"] == "sandbox_ok", cid
        assert plan["sql"]
    rr.close()


def test_unnest_uses_overlay_chunks(tmp_path):
    live = write_superstore(tmp_path / "superstore.sqlite")
    rr = RevolveRelate.connect(str(live), workdir=tmp_path)
    rr.build()
    ir = chain(
        rr.schema,
        [
            {"op": "overlay", "column": "ProductName"},
            {"op": "chunk_semantic", "column": "ProductName"},
            {"op": "embed", "query": "chair", "column": "ProductName"},
        ],
    )
    validate_ir(ir, rr.schema)
    ran = rr.execute_ir(ir)
    assert ran["rows"] is not None
    causal = chain(
        rr.schema,
        [
            {"op": "overlay", "column": "ProductName"},
            {"op": "chunk_causal", "column": "ProductName"},
            {"op": "knn", "query": "chair", "n": 3, "column": "ProductName"},
        ],
    )
    validate_ir(causal, rr.schema)
    assert rr.execute_ir(causal)["rows"] is not None
    rr.close()
