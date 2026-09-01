"""Load the language-agnostic engine catalog from spec/engines.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


def spec_dir() -> Path:
    env = __import__("os").environ.get("REVOLVERELATE_SPEC")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    root = here.parents[3]
    return root / "spec"


def _load_engines() -> list[dict]:
    path = spec_dir() / "engines.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("engines") or [])


@dataclass(frozen=True)
class Engine:
    id: str
    family: str
    aliases: tuple[str, ...]
    schemes: tuple[str, ...]
    quoting: str
    limit_style: str
    introspect: str
    readonly_session: tuple[str, ...]
    optimizer: tuple[str, ...]
    description: str
    emit_family: str
    placeholder: str
    execute_tier: str
    connection_family: str

    @classmethod
    def from_dict(cls, row: dict) -> Engine:
        return cls(
            id=row["id"],
            family=row.get("family", "sql"),
            aliases=tuple(row.get("aliases") or ()),
            schemes=tuple(row.get("schemes") or (row["id"],)),
            quoting=row.get("quoting", "double"),
            limit_style=row.get("limitStyle", "limit"),
            introspect=row.get("introspect", "information_schema"),
            readonly_session=tuple(row.get("readonlySession") or ()),
            optimizer=tuple(row.get("optimizer") or ()),
            description=row.get("description", ""),
            emit_family=row.get("emitFamily", "generic"),
            placeholder=row.get("placeholder", "question"),
            execute_tier=row.get("executeTier", "C"),
            connection_family=row.get("connectionFamily", row.get("family", "sql")),
        )


def _index() -> tuple[tuple[Engine, ...], dict[str, Engine]]:
    engines = tuple(Engine.from_dict(row) for row in _load_engines())
    by: dict[str, Engine] = {}
    for eng in engines:
        by[eng.id] = eng
        for alias in eng.aliases + eng.schemes:
            by.setdefault(alias, eng)
    return engines, by


ENGINES, _BY = _index()


class EngineError(Exception):
    pass


def get_engine(name: str) -> Engine:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    # emit-family names (postgres, mysql, tds...) used by golden fixtures
    for eng in ENGINES:
        if eng.emit_family == key:
            return eng
    engine = _BY.get(key)
    if engine is None:
        known = ", ".join(e.id for e in ENGINES[:12])
        raise EngineError(f"Unknown engine {name!r}. Catalog has {len(ENGINES)} engines, e.g. {known}...")
    return engine


def list_engines() -> list[Engine]:
    return list(ENGINES)


def quote_ident(engine: Engine, name: str) -> str:
    if name == "*":
        return "*"
    quoting = engine.quoting
    if engine.emit_family == "bigquery":
        quoting = "backtick"
    if quoting == "backtick":
        return f"`{name.replace('`', '``')}`"
    if quoting == "bracket":
        return f"[{name.replace(']', ']]')}]"
    if quoting == "none":
        return name
    return f'"{name.replace('"', '""')}"'


def placeholder(engine: Engine, index: int) -> str:
    style = engine.placeholder
    if engine.emit_family == "bigquery":
        style = "at"
    if style == "dollar":
        return f"${index}"
    if style == "at":
        return f"@p{index}"
    if style == "colon":
        return f":p{index}"
    return "?"
