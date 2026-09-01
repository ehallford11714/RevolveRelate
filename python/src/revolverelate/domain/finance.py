"""Equities price-move sample: yfinance daily bars when available, baked bars otherwise.

Moves are flagged from the bars themselves (return z-score, volume ratio, gap,
trend regime) and described with templated cue text so chunk_causal can pair
them. Cues restate measured facts; they are not causal claims. Never SQL from
an SLM. Not a forecast, not investment advice.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from revolverelate.catalog import spec_dir

_ANCHOR = date(2026, 8, 31)


@lru_cache(maxsize=1)
def load_finance_spec() -> dict:
    return json.loads((spec_dir() / "domain-finance.json").read_text(encoding="utf-8"))


def _moves_cfg() -> dict:
    return dict(load_finance_spec().get("moves") or {})


# ---------------------------------------------------------------- bars


def fetch_bars_yfinance(symbol: str, *, period: str | None = None) -> list[dict] | None:
    """Daily OHLCV from yfinance. None when the package or network is unavailable."""
    try:
        import logging

        import yfinance as yf  # type: ignore

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    except Exception:
        return None
    try:
        hist = yf.Ticker(symbol).history(period=period or _moves_cfg().get("period") or "1y", auto_adjust=True)
    except Exception:
        return None
    rows: list[dict] = []
    try:
        for idx, r in hist.iterrows():
            close = float(r.get("Close"))
            if math.isnan(close):
                continue
            rows.append(
                {
                    "date": idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10],
                    "open": float(r.get("Open")),
                    "high": float(r.get("High")),
                    "low": float(r.get("Low")),
                    "close": close,
                    "volume": float(r.get("Volume") or 0.0),
                }
            )
    except Exception:
        return None
    return rows if len(rows) >= 30 else None


def _business_days(n: int, *, end: date = _ANCHOR) -> list[date]:
    days: list[date] = []
    cur = end
    while len(days) < n:
        if cur.weekday() < 5:
            days.append(cur)
        cur -= timedelta(days=1)
    return list(reversed(days))


def bake_bars(symbol: str, *, days: int | None = None, seed: int = 11) -> list[dict]:
    """Seeded random walk with a few injected shock sessions so moves exist offline."""
    cfg = _moves_cfg()
    n = int(days or cfg.get("bakedDays") or 260)
    rng = random.Random(f"{symbol}:{seed}")
    price = 40.0 + (sum(ord(c) for c in symbol) % 200)
    base_vol = 1_000_000.0 + (sum(ord(c) for c in symbol) % 7) * 250_000.0
    shocks = {int(n * f): (s, v) for f, s, v in ((0.22, -0.061, 2.4), (0.47, 0.054, 2.1), (0.68, -0.048, 1.9), (0.86, 0.072, 3.0))}
    gap_day = int(n * 0.33)
    out: list[dict] = []
    drift = 0.0004
    for i, d in enumerate(_business_days(n)):
        ret = rng.gauss(drift, 0.011)
        vol_mult = max(0.4, rng.gauss(1.0, 0.25))
        if i in shocks:
            ret, vol_mult = shocks[i][0] + rng.gauss(0, 0.004), shocks[i][1]
        open_ = price * (1 + (0.025 if i == gap_day else rng.gauss(0, 0.002)))
        close = price * (1 + ret)
        high = max(open_, close) * (1 + abs(rng.gauss(0, 0.003)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, 0.003)))
        out.append(
            {
                "date": d.isoformat(),
                "open": round(open_, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": round(base_vol * vol_mult, 0),
            }
        )
        price = close
    return out


# ---------------------------------------------------------------- moves + cues


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def flag_moves(bars: list[dict], *, cfg: dict | None = None) -> list[dict]:
    """Return z-score, volume ratio, gap, regime for every bar; flag moves past zThreshold."""
    cfg = cfg or _moves_cfg()
    window = int(cfg.get("window") or 20)
    z_thr = float(cfg.get("zThreshold") or 2.0)
    regime_w = int(cfg.get("regimeWindow") or 50)
    rets: list[float] = []
    out: list[dict] = []
    for i, bar in enumerate(bars):
        prev = bars[i - 1] if i > 0 else None
        ret = (bar["close"] / prev["close"] - 1.0) if prev and prev["close"] else 0.0
        gap = (bar["open"] / prev["close"] - 1.0) if prev and prev["close"] else 0.0
        hist = rets[-window:]
        z = (ret - _mean(hist)) / _std(hist) if len(hist) >= window and _std(hist) > 0 else 0.0
        vols = [b["volume"] for b in bars[max(0, i - window) : i]]
        vr = (bar["volume"] / _mean(vols)) if vols and _mean(vols) > 0 else 1.0
        closes = [b["close"] for b in bars[max(0, i - regime_w) : i]]
        regime = "bull" if closes and bar["close"] >= _mean(closes) else "bear"
        row = {**bar, "return": ret, "absReturn": abs(ret), "z": z, "volumeRatio": vr, "gap": gap, "regime": regime, "isMove": abs(z) >= z_thr}
        out.append(row)
        rets.append(ret)
    return out


def _fmt_pct(x: float) -> str:
    return f"{abs(x) * 100:.1f}"


def cue_note(symbol: str, row: dict, *, after_event: str | None, cfg: dict | None = None) -> tuple[str, str]:
    """Pick one templated cue and fill it from measured bar facts. Returns (cue, note)."""
    cfg = cfg or _moves_cfg()
    cues = load_finance_spec().get("cues") or {}
    verb = "rose" if row["return"] >= 0 else "fell"
    direction = "up" if row["return"] >= 0 else "down"
    fill = {
        "symbol": symbol,
        "verb": verb,
        "pct": _fmt_pct(row["return"]),
        "date": row["date"],
        "vr": f"{row['volumeRatio']:.1f}",
        "window": int(cfg.get("window") or 20),
        "direction": direction,
        "regime": row["regime"],
        "regimeWindow": int(cfg.get("regimeWindow") or 50),
        "gap": _fmt_pct(row["gap"]),
        "kind": after_event or "event",
    }
    if after_event:
        key = "event"
    elif abs(row["gap"]) >= float(cfg.get("gapPct") or 0.02):
        key = "gap"
    elif row["volumeRatio"] >= float(cfg.get("volumeSpike") or 1.5):
        key = "volume"
    else:
        key = "regime"
    template = str(cues.get(key) or cues.get("regime") or "{symbol} {verb} {pct}% on {date}.")
    return key, template.format(**fill)


def event_dates(symbol: str, bars: list[dict], *, use_yfinance: bool) -> list[dict]:
    """Earnings dates from yfinance when available; otherwise catalogued quarterly stand-ins."""
    found: list[dict] = []
    if use_yfinance:
        try:
            import yfinance as yf  # type: ignore

            ed = yf.Ticker(symbol).earnings_dates
            for idx in list(getattr(ed, "index", []))[:8]:
                d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                found.append({"date": d, "kind": "earnings", "source": "yfinance"})
        except Exception:
            found = []
    if not found and bars:
        step = max(len(bars) // 4, 1)
        for k in range(1, 4):
            i = min(k * step, len(bars) - 1)
            found.append({"date": bars[i]["date"], "kind": "earnings", "source": "baked"})
    for ev in found:
        ev["headline"] = f"{symbol} catalogued {ev['kind']} date {ev['date']}; therefore the next session may react."
    return found


# ---------------------------------------------------------------- sqlite


_DDL = """
CREATE TABLE IF NOT EXISTS Ticker (
    TickerId INTEGER PRIMARY KEY,
    Symbol TEXT NOT NULL,
    Name TEXT,
    Sector TEXT,
    Industry TEXT,
    Peers TEXT,
    Source TEXT
);
CREATE TABLE IF NOT EXISTS PriceBar (
    BarId INTEGER PRIMARY KEY,
    TickerId INTEGER NOT NULL,
    BarDate DATE,
    Open REAL,
    High REAL,
    Low REAL,
    Close REAL,
    Volume REAL,
    DayReturn REAL,
    FOREIGN KEY (TickerId) REFERENCES Ticker(TickerId)
);
CREATE TABLE IF NOT EXISTS PriceMove (
    MoveId INTEGER PRIMARY KEY,
    TickerId INTEGER NOT NULL,
    MoveDate DATE,
    Return REAL,
    AbsReturn REAL,
    ZScore REAL,
    VolumeRatio REAL,
    GapPct REAL,
    MoveClose REAL,
    MoveVolume REAL,
    Direction TEXT,
    Regime TEXT,
    Cue TEXT,
    Note TEXT,
    FOREIGN KEY (TickerId) REFERENCES Ticker(TickerId)
);
CREATE TABLE IF NOT EXISTS MarketEvent (
    EventId INTEGER PRIMARY KEY,
    TickerId INTEGER NOT NULL,
    EventDate DATE,
    Kind TEXT,
    Headline TEXT,
    Source TEXT,
    FOREIGN KEY (TickerId) REFERENCES Ticker(TickerId)
);
"""


def _catalog_rows() -> list[dict]:
    spec = load_finance_spec()
    return [r for r in list(spec.get("universe") or []) + list(spec.get("followOn") or []) if isinstance(r, dict)]


def _peers(symbol: str, sector: str) -> str:
    peers = [r["symbol"] for r in _catalog_rows() if r.get("sector") == sector and r.get("symbol") != symbol]
    return " ".join(peers)


def _next_id(conn, table: str, col: str) -> int:
    return int(conn.execute(f"SELECT COALESCE(MAX({col}), 0) FROM {table}").fetchone()[0]) + 1


def _load_symbol(conn, rec: dict, *, use_yfinance: bool, period: str | None, seed: int) -> str:
    symbol = str(rec.get("symbol") or "").strip().upper()
    bars = fetch_bars_yfinance(symbol, period=period) if use_yfinance else None
    source = "yfinance" if bars else "baked"
    if not bars:
        bars = bake_bars(symbol, seed=seed)
    tid = _next_id(conn, "Ticker", "TickerId")
    conn.execute(
        "INSERT INTO Ticker VALUES (?,?,?,?,?,?,?)",
        (tid, symbol, str(rec.get("name") or symbol), str(rec.get("sector") or ""), str(rec.get("industry") or ""), _peers(symbol, str(rec.get("sector") or "")), source),
    )
    flagged = flag_moves(bars)
    bar_id = _next_id(conn, "PriceBar", "BarId")
    for row in flagged:
        conn.execute(
            "INSERT INTO PriceBar VALUES (?,?,?,?,?,?,?,?,?)",
            (bar_id, tid, row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"], row["return"]),
        )
        bar_id += 1
    events = event_dates(symbol, bars, use_yfinance=source == "yfinance")
    ev_dates = {e["date"]: e["kind"] for e in events}
    ev_id = _next_id(conn, "MarketEvent", "EventId")
    for ev in events:
        conn.execute("INSERT INTO MarketEvent VALUES (?,?,?,?,?,?)", (ev_id, tid, ev["date"], ev["kind"], ev["headline"], ev["source"]))
        ev_id += 1
    move_id = _next_id(conn, "PriceMove", "MoveId")
    for i, row in enumerate(flagged):
        if not row["isMove"]:
            continue
        prev_date = flagged[i - 1]["date"] if i > 0 else ""
        after = ev_dates.get(prev_date) or ev_dates.get(row["date"])
        cue, note = cue_note(symbol, row, after_event=after)
        conn.execute(
            "INSERT INTO PriceMove VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                move_id,
                tid,
                row["date"],
                round(row["return"], 6),
                round(row["absReturn"], 6),
                round(row["z"], 4),
                round(row["volumeRatio"], 4),
                round(row["gap"], 6),
                row["close"],
                row["volume"],
                "up" if row["return"] >= 0 else "down",
                row["regime"],
                cue,
                note,
            ),
        )
        move_id += 1
    return symbol


def write_finance_equities(
    path: str | Path,
    *,
    symbols: list[str] | None = None,
    period: str | None = None,
    use_yfinance: bool = True,
    seed: int = 11,
) -> Path:
    """Write a small equities sqlite the agent can rr_boot and ask. Universe from spec/domain-finance.json."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    spec = load_finance_spec()
    wanted = [s.upper() for s in (symbols or [r["symbol"] for r in spec.get("universe") or []])]
    by_symbol = {r["symbol"].upper(): r for r in _catalog_rows()}
    conn = sqlite3.connect(str(dest))
    conn.executescript(_DDL)
    for sym in wanted:
        rec = by_symbol.get(sym) or {"symbol": sym, "name": sym, "sector": "", "industry": ""}
        _load_symbol(conn, rec, use_yfinance=use_yfinance, period=period, seed=seed)
    conn.commit()
    conn.close()
    return dest


def list_symbols(conn) -> set[str]:
    try:
        rows = conn.execute("SELECT Symbol FROM Ticker").fetchall()
    except Exception:
        return set()
    return {str(r[0]) for r in rows if r and r[0]}


def append_follow_on(conn, records: list[dict]) -> list[str]:
    """Load catalogued peer tickers into an existing equities sqlite. Same source mode as the existing rows."""
    have = list_symbols(conn)
    try:
        sources = {str(r[0]) for r in conn.execute("SELECT DISTINCT Source FROM Ticker").fetchall()}
    except Exception:
        sources = set()
    use_yf = "yfinance" in sources
    added: list[str] = []
    for rec in records or []:
        symbol = str(rec.get("symbol") or "").strip().upper()
        if not symbol or symbol in have:
            continue
        _load_symbol(conn, rec, use_yfinance=use_yf, period=None, seed=11)
        have.add(symbol)
        added.append(symbol)
    conn.commit()
    return added
