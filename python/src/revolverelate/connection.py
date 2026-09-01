"""DSN parsing. Secrets never appear in repr."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from revolverelate.catalog import Engine, get_engine
from revolverelate.errors import EngineError


def redact_dsn(dsn: str) -> str:
    import re

    text = dsn or ""
    text = re.sub(r"(://[^:/?#\s]+:)([^@/]+)(@)", r"\1***\3", text)
    text = re.sub(
        r"(?i)([?&](?:password|passwd|pwd|secret|token|api[_-]?key)=)([^&]*)",
        r"\1***",
        text,
    )
    return text


@dataclass
class ConnectionSpec:
    engine: Engine
    dsn: str = field(repr=False)
    database: str
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    path: str | None = None
    query: dict[str, str] = field(default_factory=dict)
    readonly: bool = False
    sslmode: str = "verify-full"

    @property
    def redacted_dsn(self) -> str:
        return redact_dsn(self.dsn)

    def __repr__(self) -> str:
        return (
            f"ConnectionSpec(engine={self.engine.id!r}, host={self.host!r}, "
            f"database={self.database!r}, user={self.user!r}, password='***')"
        )


def parse_dsn(dsn: str, *, engine: str | None = None, readonly: bool = False) -> ConnectionSpec:
    raw = dsn.strip()
    if not raw:
        raise EngineError("Empty connection string")
    if engine:
        eng = get_engine(engine)
        return ConnectionSpec(engine=eng, dsn=raw, database=raw, readonly=readonly)
    if "://" not in raw:
        path = str(Path(raw).expanduser())
        eng = get_engine("sqlite")
        return ConnectionSpec(
            engine=eng,
            dsn=f"sqlite:///{path}",
            database=path,
            path=path,
            readonly=readonly,
            sslmode="n/a",
        )
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower().split("+")[0]
    if scheme in {"file", "sqlite3"}:
        scheme = "sqlite"
    eng = get_engine(scheme)
    database = unquote((parsed.path or "").lstrip("/"))
    path = None
    if eng.id == "sqlite":
        if parsed.path:
            path = unquote(parsed.path)
            if path.startswith("/") and len(path) > 2 and path[2] == ":":
                path = path[1:]
        else:
            path = database or ":memory:"
        database = path or ":memory:"
    q = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
    return ConnectionSpec(
        engine=eng,
        dsn=raw,
        database=database or (parsed.hostname or ""),
        host=parsed.hostname,
        port=parsed.port,
        user=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        path=path,
        query=q,
        readonly=readonly,
        sslmode=q.get("sslmode") or ("n/a" if eng.id == "sqlite" else "verify-full"),
    )
