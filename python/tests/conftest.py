from __future__ import annotations

import os
import sqlite3
from pathlib import Path

os.environ["REVOLVERELATE_SLM"] = "0"

import pytest

from revolverelate.schema.model import Attribute, Entity, Relationship, SchemaGraph


def provided_schema() -> SchemaGraph:
    graph = SchemaGraph(engine="sqlite", dialect="sqlite")
    graph.add_entity(
        Entity(
            "Customer",
            attributes=(
                Attribute("CustomerId", "INTEGER", nullable=False, primary_key=True),
                Attribute("LastName", "TEXT"),
                Attribute("Country", "TEXT"),
                Attribute("Email", "TEXT", sensitivity="pii"),
                Attribute("Password", "TEXT", sensitivity="critical"),
            ),
        )
    )
    graph.add_entity(
        Entity(
            "Invoice",
            attributes=(
                Attribute("InvoiceId", "INTEGER", nullable=False, primary_key=True),
                Attribute("CustomerId", "INTEGER", nullable=False),
                Attribute("Total", "REAL"),
            ),
        )
    )
    graph.add_relationship(
        Relationship(
            "Invoice.CustomerId->Customer",
            from_entity="Invoice",
            from_attrs=("CustomerId",),
            to_entity="Customer",
            to_attrs=("CustomerId",),
        )
    )
    return graph


def seed_live_sqlite(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, LastName TEXT, Country TEXT, Email TEXT, Password TEXT)"
    )
    conn.execute(
        "CREATE TABLE Invoice (InvoiceId INTEGER PRIMARY KEY, CustomerId INTEGER, Total REAL)"
    )
    conn.executemany(
        "INSERT INTO Customer VALUES (?,?,?,?,?)",
        [
            (1, "Adams", "Canada", "a@example.com", "secret1"),
            (2, "Baker", "USA", "b@example.com", "secret2"),
        ],
    )
    conn.executemany(
        "INSERT INTO Invoice VALUES (?,?,?)",
        [(10, 1, 12.5), (11, 2, 4.0)],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def schema() -> SchemaGraph:
    return provided_schema()


@pytest.fixture
def live_db(tmp_path: Path) -> Path:
    return seed_live_sqlite(tmp_path / "live.sqlite")
