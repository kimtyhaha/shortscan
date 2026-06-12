"""yfinance로 종목명 일괄 조회 → DB 캐시.

사용법:
    python -m collector.fetch_names          # DB 기준 미등록 종목만
    python -m collector.fetch_names --all    # 전체 재조회
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT    = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "shortscan.db"

MAX_WORKERS = 30
BATCH_LOG   = 200   # N개마다 진행상황 출력


def _fetch_one(symbol: str) -> tuple[str, str]:
    """yfinance로 단일 종목 회사명 조회. 실패 시 빈 문자열."""
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).fast_info
        # fast_info에는 longName 없음 → .info 사용
        full = yf.Ticker(symbol).info
        name = full.get("longName") or full.get("shortName") or ""
        return symbol, name.strip()
    except Exception:
        return symbol, ""


def fetch_missing(conn: sqlite3.Connection, symbols: list[str],
                  force: bool = False) -> int:
    """DB에 없는 종목만 yfinance 조회 후 저장. 저장된 건수 반환."""
    if force:
        missing = symbols
    else:
        cached = {
            row[0] for row in
            conn.execute("SELECT symbol FROM company_names").fetchall()
        }
        missing = [s for s in symbols if s not in cached]

    if not missing:
        print("  ✓ 회사명 캐시: 추가 조회 없음")
        return 0

    print(f"  → 회사명 조회 시작: {len(missing)}개 종목 (workers={MAX_WORKERS})")
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_one, sym): sym for sym in missing}
        rows: list[tuple[str, str, str]] = []

        for i, fut in enumerate(as_completed(futures), 1):
            sym, name = fut.result()
            rows.append((sym, name or sym, now))   # 이름 없으면 티커 그대로

            if len(rows) >= 500:
                conn.executemany(
                    "INSERT OR REPLACE INTO company_names (symbol, name, fetched_at) VALUES (?,?,?)",
                    rows,
                )
                conn.commit()
                saved += len(rows)
                rows = []

            if i % BATCH_LOG == 0:
                print(f"     {i}/{len(missing)} 완료...")

        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO company_names (symbol, name, fetched_at) VALUES (?,?,?)",
                rows,
            )
            conn.commit()
            saved += len(rows)

    print(f"  ✓ 회사명 캐시 저장: {saved}개")
    return saved


def get_cached_name(conn: sqlite3.Connection, symbol: str) -> str | None:
    """DB 캐시에서 회사명 조회. 없으면 None."""
    row = conn.execute(
        "SELECT name FROM company_names WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row[0] if row else None


def _fetch_fundamentals(symbol: str) -> dict:
    """yfinance로 Short Interest 관련 지표 조회."""
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        return {
            "symbol":          symbol,
            "short_pct_float": info.get("shortPercentOfFloat"),
            "shares_short":    info.get("sharesShort"),
            "short_ratio":     info.get("shortRatio"),
            "float_shares":    info.get("floatShares"),
        }
    except Exception:
        return {"symbol": symbol}


def import_fundamentals_json(conn: sqlite3.Connection) -> int:
    """data/short_interest.json → DB import. 이미 있는 종목은 덮어씀."""
    json_path = ROOT / "data" / "short_interest.json"
    if not json_path.exists():
        return 0
    import json
    data = json.loads(json_path.read_text())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [(d["s"], d.get("p"), d.get("n"), d.get("r"), None, now) for d in data]
    conn.executemany(
        """INSERT OR REPLACE INTO stock_fundamentals
           (symbol, short_pct_float, shares_short, short_ratio, float_shares, fetched_at)
           VALUES (?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    print(f"  ✓ short_interest.json import: {len(rows)}개")
    return len(rows)


def fetch_fundamentals(conn: sqlite3.Connection, symbols: list[str],
                       force: bool = False) -> int:
    """Short Interest 지표 조회 후 DB 저장. 이미 있으면 스킵."""
    if force:
        missing = symbols
    else:
        cached = {
            row[0] for row in
            conn.execute("SELECT symbol FROM stock_fundamentals").fetchall()
        }
        missing = [s for s in symbols if s not in cached]

    if not missing:
        return 0

    print(f"  → Short Interest 조회: {len(missing)}개 (workers={MAX_WORKERS})")
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_fundamentals, sym): sym for sym in missing}
        rows: list[tuple] = []

        for i, fut in enumerate(as_completed(futures), 1):
            d = fut.result()
            rows.append((
                d["symbol"],
                d.get("short_pct_float"),
                d.get("shares_short"),
                d.get("short_ratio"),
                d.get("float_shares"),
                now,
            ))

            if len(rows) >= 200:
                conn.executemany(
                    """INSERT OR REPLACE INTO stock_fundamentals
                       (symbol, short_pct_float, shares_short, short_ratio, float_shares, fetched_at)
                       VALUES (?,?,?,?,?,?)""",
                    rows,
                )
                conn.commit()
                saved += len(rows)
                rows = []

            if i % BATCH_LOG == 0:
                print(f"     {i}/{len(missing)} 완료...")

        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO stock_fundamentals
                   (symbol, short_pct_float, shares_short, short_ratio, float_shares, fetched_at)
                   VALUES (?,?,?,?,?,?)""",
                rows,
            )
            conn.commit()
            saved += len(rows)

    print(f"  ✓ Short Interest 저장: {saved}개")
    return saved


if __name__ == "__main__":
    import argparse
    from collector.database import connect

    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="전체 재조회")
    args = parser.parse_args()

    conn = connect(DB_PATH)
    syms = [
        r[0] for r in
        conn.execute("SELECT DISTINCT symbol FROM daily_short_volume").fetchall()
    ]
    fetch_missing(conn, syms, force=args.all)
    conn.close()
