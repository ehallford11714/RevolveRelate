"""Evidence memory in the vector overlay.

Every automine pass writes its evidence rows into OverlayChunk under a virtual
entity, chunked the same way as live text (semantic + causal), so the next pass
can recall prior evidence with the same RelOp knn the rest of the engine uses.
Chroma (when opted in) syncs from the same table. Dummy only: evidence is never
a live table.
"""

from __future__ import annotations

import time

from revolverelate.vector.chunk import chunk_text
from revolverelate.vector.overlay import OVERLAY, _insert_pieces

EVIDENCE_ENTITY = "AutomineEvidence"
EVIDENCE_COLUMN = "Evidence"
_STRATEGIES = ("semantic", "causal")


def _conn(sandbox):
    return getattr(sandbox, "_conn", sandbox)


def _next_chunk_id(conn) -> int:
    try:
        row = conn.execute(f'SELECT COALESCE(MAX(ChunkId), 0) FROM "{OVERLAY}"').fetchone()
        return int(row[0] if row else 0) + 1
    except Exception:
        return 1


def evidence_text(row: dict) -> str:
    cand = str(row.get("candidate") or "").strip()
    cause = str(row.get("cause") or "").strip()
    effect = str(row.get("effect") or "").strip()
    cue = str(row.get("cue") or "").strip()
    if cause and effect and cue and cue != "catalog":
        body = f"{cause} {cue} {effect}"
    elif cause or effect:
        body = f"{cause} {effect}".strip()
    else:
        body = f"{cand} was proposed from live text as a catalogued candidate."
    if cand and cand.casefold() not in body.casefold():
        body = f"{cand}: {body}"
    return body


def remember_evidence(sandbox, rows: list[dict], *, domain: str, question: str, pass_no: int) -> int:
    """Write evidence rows as overlay chunks. Returns chunks inserted. Idempotent per (domain, candidate, cue, pass)."""
    conn = _conn(sandbox)
    if conn is None or not rows:
        return 0
    try:
        conn.execute(f'SELECT 1 FROM "{OVERLAY}" LIMIT 1')
    except Exception:
        return 0
    have = {
        str(r[0])
        for r in conn.execute(f'SELECT DISTINCT SourcePk FROM "{OVERLAY}" WHERE Entity = ?', (EVIDENCE_ENTITY,)).fetchall()
    }
    rid = _next_chunk_id(conn)
    inserted = 0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        pk = f"{domain}:{row.get('candidate')}:{row.get('cue')}:{row.get('pass') or pass_no}:{row.get('source', {}).get('pk') or i}"
        if pk in have:
            continue
        body = evidence_text(row)
        for strategy in _STRATEGIES:
            pieces = chunk_text(body, strategy)
            for piece in pieces:
                piece.setdefault("role", "evidence")
            rid, n = _insert_pieces(conn, rid, EVIDENCE_ENTITY, pk, EVIDENCE_COLUMN, strategy, pieces)
            inserted += n
        have.add(pk)
    if hasattr(conn, "commit"):
        conn.commit()
    return inserted


def recall_evidence(rr, question: str, *, n: int = 5, strategy: str = "semantic") -> dict:
    """RelOp knn over remembered evidence chunks. Dummy only; there is no live evidence table."""
    steps = [
        {"op": "overlay", "column": EVIDENCE_COLUMN, "entity": EVIDENCE_ENTITY},
        {"op": f"chunk_{strategy}", "column": EVIDENCE_COLUMN},
        {"op": "knn", "query": question, "n": int(n), "column": EVIDENCE_COLUMN},
    ]
    try:
        plan = rr.analytics.run_chain(steps, plan_id=f"automine-recall-{strategy}")
    except Exception as exc:
        return {"ran": False, "error": str(exc)[:200], "rows": [], "rowCount": 0, "steps": steps}
    cols = [str(c) for c in plan.get("columns") or []]
    idx = {c.casefold(): i for i, c in enumerate(cols)}
    hits = []
    for row in plan.get("rows") or []:
        text = row[idx["text"]] if "text" in idx and idx["text"] < len(row) else (row[0] if row else "")
        pk = row[idx["sourcepk"]] if "sourcepk" in idx and idx["sourcepk"] < len(row) else ""
        hits.append({"text": str(text or "")[:240], "sourcePk": str(pk or "")})
    return {
        "ran": plan.get("status") == "sandbox_ok",
        "planId": plan.get("id"),
        "rowCount": int(plan.get("rowCount") or 0),
        "rows": hits,
        "columns": cols,
        "steps": steps,
        "target": "sandbox",
        "note": "Evidence memory is a dummy overlay. Same RelOp knn as live text; never a live table.",
    }


def evidence_stats(sandbox) -> dict:
    conn = _conn(sandbox)
    try:
        chunks = int(conn.execute(f'SELECT COUNT(*) FROM "{OVERLAY}" WHERE Entity = ?', (EVIDENCE_ENTITY,)).fetchone()[0])
        rows = int(conn.execute(f'SELECT COUNT(DISTINCT SourcePk) FROM "{OVERLAY}" WHERE Entity = ?', (EVIDENCE_ENTITY,)).fetchone()[0])
    except Exception:
        chunks, rows = 0, 0
    return {"entity": EVIDENCE_ENTITY, "column": EVIDENCE_COLUMN, "chunks": chunks, "evidenceRows": rows, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def dump_evidence(sandbox, *, limit: int = 50) -> list[dict]:
    conn = _conn(sandbox)
    try:
        rows = conn.execute(
            f'SELECT SourcePk, Strategy, Text, Cue, Role FROM "{OVERLAY}" WHERE Entity = ? ORDER BY ChunkId LIMIT ?',
            (EVIDENCE_ENTITY, int(limit)),
        ).fetchall()
    except Exception:
        return []
    return [{"sourcePk": r[0], "strategy": r[1], "text": r[2], "cue": r[3], "role": r[4]} for r in rows]
