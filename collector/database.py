"""SQLite 저장 계층. 일별 공매도 거래량 + (추후) 격주 잔고."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from collector.finra_daily import ShortVolumeRecord

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_short_volume (
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    short_volume INTEGER NOT NULL,
    short_exempt_volume INTEGER NOT NULL,
    total_volume INTEGER NOT NULL,
    market TEXT,
    PRIMARY KEY (trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_dsv_symbol ON daily_short_volume(symbol);

CREATE TABLE IF NOT EXISTS short_interest (
    settlement_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    short_interest INTEGER NOT NULL,
    avg_daily_volume INTEGER,
    days_to_cover REAL,
    PRIMARY KEY (settlement_date, symbol)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def upsert_daily(conn: sqlite3.Connection, records: list[ShortVolumeRecord]) -> int:
    rows = [
        (r.trade_date, r.symbol, r.short_volume, r.short_exempt_volume, r.total_volume, r.market)
        for r in records
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO daily_short_volume
           (trade_date, symbol, short_volume, short_exempt_volume, total_volume, market)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def recent_ratios(conn: sqlite3.Connection, symbol: str, days: int = 10) -> list[tuple[str, float]]:
    """종목의 최근 N거래일 (날짜, 공매도비율%) — 오래된 날짜부터."""
    cur = conn.execute(
        """SELECT trade_date,
                  ROUND(CAST(short_volume AS REAL) / NULLIF(total_volume, 0) * 100, 2)
           FROM daily_short_volume
           WHERE symbol = ? AND total_volume > 0
           ORDER BY trade_date DESC LIMIT ?""",
        (symbol.upper(), days),
    )
    rows = cur.fetchall()
    return list(reversed(rows))


def top_ratio_surge(conn: sqlite3.Connection, trade_date: str, limit: int = 20,
                    min_total_volume: int = 500_000) -> list[dict]:
    """당일 vs 전 거래일 공매도 비율 상승폭 상위 종목(저유동성 노이즈 제외)."""
    cur = conn.execute(
        """WITH ranked AS (
               SELECT trade_date, symbol,
                      CAST(short_volume AS REAL) / NULLIF(total_volume, 0) * 100 AS ratio,
                      total_volume,
                      LAG(CAST(short_volume AS REAL) / NULLIF(total_volume, 0) * 100)
                          OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_ratio
               FROM daily_short_volume
           )
           SELECT symbol, ROUND(ratio, 1), ROUND(ratio - prev_ratio, 1), total_volume
           FROM ranked
           WHERE trade_date = ? AND prev_ratio IS NOT NULL AND total_volume >= ?
           ORDER BY (ratio - prev_ratio) DESC LIMIT ?""",
        (trade_date, min_total_volume, limit),
    )
    return [
        {"symbol": s, "ratio": r, "change": c, "total_volume": v}
        for s, r, c, v in cur.fetchall()
    ]
