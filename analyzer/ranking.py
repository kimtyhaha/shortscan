"""랭킹 산출 모듈.

제공하는 랭킹:
  1. 급증 TOP 20  — 전일 대비 공매도 비율 상승폭 상위 (노이즈 필터 적용)
  2. 고비율 TOP 20 — 당일 절대 공매도 비율 상위
  3. 숏스퀴즈 후보 — 고비율 + 최근 비율 하락 전환 조합
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from collector.kr_names import get_kr_name


@dataclass
class RankRow:
    rank: int
    symbol: str
    name_kr: str
    ratio: float           # 공매도 비율 %
    change: float | None   # 전일 대비 %p (급증 랭킹만)
    total_volume: int
    shares_short: int | None = None   # 공매잔량 (yfinance Short Interest)
    label: str = ""        # 숏스퀴즈 후보 라벨


def surge_ranking(
    conn: sqlite3.Connection,
    trade_date: str,
    limit: int = 20,
    min_total_volume: int = 1_000_000,   # 100만주 이상 — 노이즈 제거
    min_change: float = 3.0,             # 3%p 이상 상승만
) -> list[RankRow]:
    """당일 공매도 비율 급증 TOP N."""
    cur = conn.execute(
        """WITH ranked AS (
               SELECT trade_date, symbol,
                      CAST(short_volume AS REAL) / NULLIF(total_volume, 0) * 100 AS ratio,
                      total_volume,
                      LAG(CAST(short_volume AS REAL) / NULLIF(total_volume, 0) * 100)
                          OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_ratio
               FROM daily_short_volume
           )
           SELECT r.symbol,
                  ROUND(r.ratio, 1),
                  ROUND(r.ratio - r.prev_ratio, 1),
                  r.total_volume,
                  f.shares_short
           FROM ranked r
           LEFT JOIN stock_fundamentals f ON r.symbol = f.symbol
           WHERE r.trade_date = ?
             AND r.prev_ratio IS NOT NULL
             AND r.total_volume >= ?
             AND (r.ratio - r.prev_ratio) >= ?
           ORDER BY (r.ratio - r.prev_ratio) DESC
           LIMIT ?""",
        (trade_date, min_total_volume, min_change, limit),
    )
    return [
        RankRow(
            rank=i + 1,
            symbol=s,
            name_kr=get_kr_name(s, conn),
            ratio=r,
            change=c,
            total_volume=v,
            shares_short=ss,
        )
        for i, (s, r, c, v, ss) in enumerate(cur.fetchall())
    ]


def high_ratio_ranking(
    conn: sqlite3.Connection,
    trade_date: str,
    limit: int = 20,
    min_total_volume: int = 1_000_000,
    min_ratio: float = 50.0,             # 50% 이상만
) -> list[RankRow]:
    """당일 공매도 비율 절대값 상위 TOP N."""
    cur = conn.execute(
        """SELECT d.symbol,
                  ROUND(CAST(d.short_volume AS REAL) / NULLIF(d.total_volume, 0) * 100, 1),
                  d.total_volume,
                  f.shares_short
           FROM daily_short_volume d
           LEFT JOIN stock_fundamentals f ON d.symbol = f.symbol
           WHERE d.trade_date = ?
             AND d.total_volume >= ?
             AND CAST(d.short_volume AS REAL) / NULLIF(d.total_volume, 0) * 100 >= ?
           ORDER BY CAST(d.short_volume AS REAL) / NULLIF(d.total_volume, 0) DESC
           LIMIT ?""",
        (trade_date, min_total_volume, min_ratio, limit),
    )
    return [
        RankRow(
            rank=i + 1,
            symbol=s,
            name_kr=get_kr_name(s, conn),
            ratio=r,
            change=None,
            total_volume=v,
            shares_short=ss,
        )
        for i, (s, r, v, ss) in enumerate(cur.fetchall())
    ]


def squeeze_candidates(
    conn: sqlite3.Connection,
    trade_date: str,
    limit: int = 20,
    min_total_volume: int = 1_000_000,
    high_ratio_threshold: float = 40.0,  # 공매도 비율 40% 이상
    drop_threshold: float = -2.0,        # 전일 대비 -2%p 이상 하락 (청산 압력 완화 신호)
) -> list[RankRow]:
    """숏스퀴즈 후보: 공매도 비율 높지만 최근 하락 전환 종목.

    원리: 공매도 잔고가 높은 상태(short_ratio >= 40%)에서
    비율이 하락(-2%p 이하)한다는 것은 공매도 세력이 포지션을 줄이거나
    매수 청산(커버링)에 들어간 신호일 수 있음.
    → 추가 가격 상승 시 강제 청산(숏스퀴즈) 가능성 높음.

    ⚠️ FINRA 보고 기준이므로 실제 시장 전체 공매도를 반영하지 않음.
    """
    cur = conn.execute(
        """WITH ranked AS (
               SELECT trade_date, symbol,
                      CAST(short_volume AS REAL) / NULLIF(total_volume, 0) * 100 AS ratio,
                      total_volume,
                      LAG(CAST(short_volume AS REAL) / NULLIF(total_volume, 0) * 100)
                          OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_ratio
               FROM daily_short_volume
           )
           SELECT r.symbol,
                  ROUND(r.ratio, 1),
                  ROUND(r.ratio - r.prev_ratio, 1),
                  r.total_volume,
                  f.shares_short
           FROM ranked r
           LEFT JOIN stock_fundamentals f ON r.symbol = f.symbol
           WHERE r.trade_date = ?
             AND r.prev_ratio IS NOT NULL
             AND r.total_volume >= ?
             AND r.ratio >= ?
             AND (r.ratio - r.prev_ratio) <= ?
           ORDER BY r.ratio DESC
           LIMIT ?""",
        (trade_date, min_total_volume, high_ratio_threshold, drop_threshold, limit),
    )
    return [
        RankRow(
            rank=i + 1,
            symbol=s,
            name_kr=get_kr_name(s, conn),
            ratio=r,
            change=c,
            total_volume=v,
            shares_short=ss,
            label="🔥 숏스퀴즈 후보",
        )
        for i, (s, r, c, v, ss) in enumerate(cur.fetchall())
    ]


def latest_trade_date(conn: sqlite3.Connection) -> str | None:
    """DB에 저장된 가장 최신 거래일."""
    cur = conn.execute("SELECT MAX(trade_date) FROM daily_short_volume")
    row = cur.fetchone()
    return row[0] if row else None


def print_ranking(rows: list[RankRow], title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    if not rows:
        print("  (해당 조건 종목 없음)")
        return
    for r in rows:
        change_str = f"  {r.change:+.1f}%p" if r.change is not None else ""
        vol_str = f"{r.total_volume / 1_000_000:.1f}M"
        label = f"  {r.label}" if r.label else ""
        print(f"  {r.rank:2d}. {r.symbol:<8s} {r.name_kr:<16s}  {r.ratio:.1f}%{change_str}  vol={vol_str}{label}")
