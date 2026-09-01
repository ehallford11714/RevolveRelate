"""Dummy AskLog — episodic memory of acts. Virtual. Never a live Superstore table."""

from __future__ import annotations

import json
import time

from revolverelate.schema.model import Attribute, Entity, SchemaGraph
from revolverelate.vector.embed import fingerprint

ASKLOG = "AskLog"


def asklog_entity() -> Entity:
    return Entity(
        name=ASKLOG,
        kind="overlay",
        comment="Dummy act log. Questions and RelOp hashes, not SQL.",
        attributes=(
            Attribute("AskId", "INTEGER", nullable=False, primary_key=True),
            Attribute("Question", "TEXT"),
            Attribute("Objective", "TEXT"),
            Attribute("RelOpHash", "INTEGER"),
            Attribute("RelOp", "TEXT"),
            Attribute("Ticket", "TEXT"),
            Attribute("Status", "TEXT"),
            Attribute("Target", "TEXT"),
            Attribute("Composite", "TEXT"),
            Attribute("Pattern", "TEXT"),
            Attribute("Score", "REAL"),
            Attribute("RowCount", "INTEGER"),
            Attribute("CreatedAt", "TEXT"),
        ),
    )


def register_asklog(graph: SchemaGraph) -> Entity:
    entity = asklog_entity()
    graph.add_virtual(entity)
    graph.annotations.setdefault("askLog", True)
    return entity


def install_asklog(sandbox, graph: SchemaGraph) -> None:
    register_asklog(graph)
    sandbox._conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{ASKLOG}" (
            AskId INTEGER PRIMARY KEY,
            Question TEXT,
            Objective TEXT,
            RelOpHash INTEGER,
            RelOp TEXT,
            Ticket TEXT,
            Status TEXT,
            Target TEXT,
            Composite TEXT,
            Pattern TEXT,
            Score REAL,
            RowCount INTEGER,
            CreatedAt TEXT
        )
        """
    )
    sandbox._conn.commit()


def record_ask(
    sandbox,
    *,
    question: str = "",
    objective: str = "",
    ir: dict | None = None,
    ticket: str = "",
    status: str = "sandbox_ok",
    target: str = "sandbox",
    composite: str = "",
    pattern: str = "",
    score: float | None = None,
    row_count: int = 0,
) -> int:
    packed = json.dumps(ir or {}, sort_keys=True, default=str)
    cur = sandbox._conn.execute(
        f'''INSERT INTO "{ASKLOG}"
        (Question, Objective, RelOpHash, RelOp, Ticket, Status, Target, Composite, Pattern, Score, RowCount, CreatedAt)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            question,
            objective or question,
            fingerprint(packed),
            packed,
            ticket,
            status,
            target,
            composite,
            pattern,
            score,
            row_count,
            time.strftime("%Y-%m-%dT%H:%M:%S"),
        ),
    )
    sandbox._conn.commit()
    return int(cur.lastrowid or 0)


def score_rows(rows: list, target: float | None = None) -> float:
    nums: list[float] = []
    for row in rows or []:
        for cell in row:
            if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                nums.append(float(cell))
                break
    if not nums:
        return float(len(rows or []))
    total = sum(abs(n) for n in nums)
    if target is None:
        return total
    return -abs(total - float(target))
