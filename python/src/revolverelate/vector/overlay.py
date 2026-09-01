"""OverlayChunk from non-PII text. Dummy stages (plus demo notes). Live is a TEMP replay of the same fields."""

from __future__ import annotations

from revolverelate.schema.model import Attribute, Entity, SchemaGraph
from revolverelate.vector.chunk import STRATEGIES, chunk_text, load_rag_spec
from revolverelate.vector.embed import DIM, MODEL, embed_row

OVERLAY = "OverlayChunk"

_SKIP_DIM = {
    "region",
    "segment",
    "category",
    "subcategory",
    "state",
    "city",
    "country",
    "shipmode",
    "status",
    "type",
    "code",
}


def overlay_entity() -> Entity:
    return Entity(
        name=OVERLAY,
        kind="overlay",
        comment="RAG overlay. Cue, Role, Text, SourcePk and the other chunk fields. Hash embeddings only.",
        attributes=(
            Attribute("ChunkId", "INTEGER", nullable=False, primary_key=True),
            Attribute("Entity", "TEXT", nullable=False),
            Attribute("SourcePk", "TEXT", nullable=False),
            Attribute("Column", "TEXT", nullable=False),
            Attribute("Strategy", "TEXT", nullable=False),
            Attribute("Ordinal", "INTEGER"),
            Attribute("Level", "TEXT"),
            Attribute("Text", "TEXT"),
            Attribute("Hash", "INTEGER"),
            Attribute("Norm", "REAL"),
            Attribute("Vec", "TEXT"),
            Attribute("Cue", "TEXT"),
            Attribute("Role", "TEXT"),
            Attribute("Score", "REAL"),
            Attribute("ParentId", "INTEGER"),
            Attribute("Model", "TEXT"),
            Attribute("Dim", "INTEGER"),
        ),
    )


def text_targets(graph: SchemaGraph) -> list[tuple]:
    rows = []
    for entity in graph.all_entities():
        if entity.kind == "overlay":
            continue
        pk = entity.pk_attrs()
        if not pk:
            continue
        for attr in entity.attributes:
            if attr.primary_key or attr.sensitivity in {"critical", "pii"}:
                continue
            t = (attr.type or "").upper()
            if t and not any(x in t for x in ("CHAR", "TEXT", "STR", "CLOB", "JSON")):
                continue
            key = attr.name.replace("_", "").casefold()
            if key in _SKIP_DIM:
                continue
            rows.append((entity, attr, pk[0]))
    return rows


def _chunk_embed():
    """MiniLM sentence vecs only when opted in. RelOp overlay always stores hash-16."""
    import os

    if os.environ.get("REVOLVERELATE_CHROMA_CHUNK", "").strip().casefold() not in {"1", "true", "yes"}:
        return None
    try:
        from revolverelate.vector.chroma_store import chroma_available, embed_sentences

        if chroma_available():
            return embed_sentences
    except Exception:
        return None
    return None


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _pk_exists(conn, entity: str, pk_col: str, pk: str) -> bool:
    try:
        row = conn.execute(
            f"SELECT 1 FROM {_ident(entity)} WHERE CAST({_ident(pk_col)} AS TEXT) = ? LIMIT 1",
            (str(pk),),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _name_like(conn, entity: str, pk_col: str, name_col: str, needle: str) -> str | None:
    try:
        row = conn.execute(
            f"SELECT {_ident(pk_col)} FROM {_ident(entity)} WHERE {_ident(name_col)} LIKE ? LIMIT 1",
            (f"%{needle}%",),
        ).fetchone()
    except Exception:
        return None
    return None if row is None else str(row[0])


def _pk_on_west_fact(conn) -> str | None:
    """A dummy ProductId that actually appears on a West order line."""
    try:
        row = conn.execute(
            """
            SELECT CAST(p.ProductId AS TEXT)
            FROM "OrderLine" ol
            JOIN "Product" p ON CAST(p.ProductId AS TEXT) = CAST(ol.ProductId AS TEXT)
            JOIN "Orders" o ON o.OrderId = ol.OrderId
            JOIN "Customer" c ON c.CustomerId = o.CustomerId
            WHERE c.Region = ?
            LIMIT 1
            """,
            ("West",),
        ).fetchone()
    except Exception:
        return None
    return None if row is None else str(row[0])


def bind_demo_source_pk(conn, graph: SchemaGraph, doc: dict) -> str:
    """Map a demo note onto a dummy entity key so attach_source can join. Never live PII."""
    entity = graph.entity(str(doc.get("entity") or "Product"))
    wanted = str(doc.get("pk") or "")
    if entity is None:
        return wanted or "1"
    pk_col = entity.pk_attrs()[0].name if entity.pk_attrs() else entity.attributes[0].name
    if wanted and _pk_exists(conn, entity.name, pk_col, wanted):
        return wanted
    mode = str(doc.get("attach") or "").casefold()
    text = str(doc.get("text") or "")
    if mode in {"westfact", "west", "slice"} or (
        not mode and ("because" in text.casefold() or "discount" in text.casefold())
    ):
        hit = _pk_on_west_fact(conn)
        if hit:
            return hit
    needles = [str(x) for x in (doc.get("needles") or []) if str(x).strip()]
    name_col = str(doc.get("column") or "")
    if name_col and any(a.name == name_col for a in entity.attributes):
        for needle in needles:
            hit = _name_like(conn, entity.name, pk_col, name_col, needle)
            if hit:
                return hit
    try:
        first = conn.execute(f"SELECT {_ident(pk_col)} FROM {_ident(entity.name)} LIMIT 1").fetchone()
    except Exception:
        first = None
    return str(first[0]) if first else (wanted or "1")


def _insert_pieces(conn, rid: int, entity: str, pk_val: str, column: str, strategy: str, pieces: list[dict]) -> tuple[int, int]:
    inserted = 0
    batch_start = rid
    for ordinal, piece in enumerate(pieces):
        packed = embed_row(piece["text"])
        parent_ord = piece.get("parent")
        parent_id = (batch_start + int(parent_ord)) if parent_ord is not None else None
        score = piece.get("score")
        conn.execute(
            f'''INSERT INTO "{OVERLAY}"
            (ChunkId, Entity, SourcePk, Column, Strategy, Ordinal, Level, Text, Hash, Norm, Vec, Cue, Role, Score, ParentId, Model, Dim)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                rid,
                entity,
                str(pk_val),
                column,
                strategy,
                ordinal,
                piece.get("level") or "chunk",
                piece["text"],
                packed["hash"],
                packed["norm"],
                packed["packed"],
                piece.get("cue") or "",
                piece.get("role") or "",
                None if score is None else float(score),
                parent_id,
                MODEL,
                DIM,
            ),
        )
        rid += 1
        inserted += 1
    return rid, inserted


_OVERLAY_DDL = f"""
            ChunkId INTEGER PRIMARY KEY,
            Entity TEXT NOT NULL,
            SourcePk TEXT NOT NULL,
            Column TEXT NOT NULL,
            Strategy TEXT NOT NULL,
            Ordinal INTEGER,
            Level TEXT,
            Text TEXT,
            Hash INTEGER,
            Norm REAL,
            Vec TEXT,
            Cue TEXT,
            Role TEXT,
            Score REAL,
            ParentId INTEGER,
            Model TEXT,
            Dim INTEGER
"""


def uses_overlay(ir: dict | None) -> bool:
    return OVERLAY in (str(ir or ""))


def register_overlay(graph: SchemaGraph) -> Entity:
    entity = overlay_entity()
    graph.add_virtual(entity)
    graph.annotations.setdefault("overlays", [])
    return entity


def _create_kind(target, *, temp: bool) -> str:
    if not temp:
        return "TABLE"
    engine = getattr(getattr(target, "spec", None), "engine", None)
    eid = str(getattr(engine, "id", "") or "").casefold()
    family = str(getattr(engine, "connection_family", "") or "").casefold()
    if "mysql" in eid or family == "mysql":
        return "TEMPORARY TABLE"
    return "TEMP TABLE"


def _raw_conn(target):
    return getattr(target, "_conn", target)


def _fill_overlay(conn, graph: SchemaGraph, policy: dict | None, *, demo_docs: bool) -> int:
    reveal = {x.casefold() for x in (policy or {}).get("reveal") or []}
    embed = _chunk_embed()
    rid = 1
    inserted = 0
    try:
        row = conn.execute(f'SELECT COALESCE(MAX(ChunkId), 0) FROM "{OVERLAY}"').fetchone()
        rid = int(row[0] if row is not None else 0) + 1
    except Exception:
        rid = 1
    for entity, attr, pk in text_targets(graph):
        if attr.sensitivity in {"critical", "pii"} and attr.name.casefold() not in reveal:
            continue
        try:
            live_rows = conn.execute(
                f'SELECT "{pk.name}", "{attr.name}" FROM "{entity.name}" WHERE "{attr.name}" IS NOT NULL'
            ).fetchall()
        except Exception:
            continue
        for pk_val, text in live_rows:
            if text is None or str(text).startswith("mask_"):
                continue
            body = str(text)
            for strategy in STRATEGIES:
                kwargs = {"embed": embed} if embed and strategy in {"semantic", "topic"} else {}
                pieces = chunk_text(body, strategy, **kwargs)
                rid, n = _insert_pieces(conn, rid, entity.name, str(pk_val), attr.name, strategy, pieces)
                inserted += n
    if demo_docs:
        for doc in load_rag_spec().get("demoDocs") or []:
            body = str(doc.get("text") or "")
            if not body:
                continue
            source_pk = bind_demo_source_pk(conn, graph, doc)
            for strategy in STRATEGIES:
                kwargs = {"embed": embed} if embed and strategy in {"semantic", "topic"} else {}
                pieces = chunk_text(body, strategy, **kwargs)
                rid, n = _insert_pieces(
                    conn,
                    rid,
                    str(doc.get("entity") or "Product"),
                    source_pk,
                    str(doc.get("column") or "ProductName"),
                    strategy,
                    pieces,
                )
                inserted += n
    if hasattr(conn, "commit"):
        conn.commit()
    graph.annotations["overlays"] = [
        {"entity": e.name, "column": a.name, "model": MODEL, "strategies": list(STRATEGIES)}
        for e, a, _ in text_targets(graph)
    ]
    return inserted


def install_overlay(sandbox, graph: SchemaGraph, policy: dict | None = None) -> int:
    """Dummy OverlayChunk: live non-PII text plus staging demo notes. Never copies live PII."""
    register_overlay(graph)
    conn = _raw_conn(sandbox)
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{OVERLAY}" ({_OVERLAY_DDL})')
    return _fill_overlay(conn, graph, policy, demo_docs=True)


def install_overlay_live(adapter, graph: SchemaGraph, policy: dict | None = None) -> int:
    """TEMP OverlayChunk on the live connection from live non-PII text. Same fields. No demo notes."""
    register_overlay(graph)
    conn = _raw_conn(adapter)
    if conn is None:
        return 0
    kind = _create_kind(adapter, temp=True)
    try:
        existing = conn.execute(f'SELECT COUNT(*) FROM "{OVERLAY}"').fetchone()
        if existing is not None and int(existing[0] or 0) > 0:
            return int(existing[0])
    except Exception:
        pass
    conn.execute(f'CREATE {kind} IF NOT EXISTS "{OVERLAY}" ({_OVERLAY_DDL})')
    return _fill_overlay(conn, graph, policy, demo_docs=False)
