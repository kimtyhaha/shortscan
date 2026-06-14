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
    """yfinance로 Short Interest 관련 지표 조회.

    ETF는 floatShares 없음 → totalAssets / navPrice 로 유통주식수 추정.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info

        float_shares = info.get("floatShares")

        # ETF: floatShares 없으면 AUM / NAV 로 추정
        if float_shares is None and info.get("quoteType") == "ETF":
            total_assets = info.get("totalAssets")
            nav = info.get("navPrice") or info.get("regularMarketPrice")
            if total_assets and nav and nav > 0:
                float_shares = int(total_assets / nav)

        return {
            "symbol":          symbol,
            "short_pct_float": info.get("shortPercentOfFloat"),
            "shares_short":    info.get("sharesShort"),
            "short_ratio":     info.get("shortRatio"),
            "float_shares":    float_shares,
            "quote_type":      info.get("quoteType"),
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
    rows = [(d["s"], d.get("p"), d.get("n"), d.get("r"), d.get("f"), d.get("q"), now) for d in data]
    conn.executemany(
        """INSERT OR REPLACE INTO stock_fundamentals
           (symbol, short_pct_float, shares_short, short_ratio, float_shares, quote_type, fetched_at)
           VALUES (?,?,?,?,?,?,?)""",
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
        # ETF 중 float_shares가 NULL인 것도 재조회 (AUM/NAV 추정 로직 적용)
        etf_no_float = {
            row[0] for row in
            conn.execute(
                "SELECT symbol FROM stock_fundamentals WHERE quote_type='ETF' AND float_shares IS NULL"
            ).fetchall()
        }
        # quote_type NULL = 초기 조회 실패 → 재조회 (FINRA 잔고 있는 것 우선, 최대 1000개)
        fetch_failed = {
            row[0] for row in
            conn.execute(
                "SELECT symbol FROM stock_fundamentals WHERE quote_type IS NULL"
            ).fetchall()
        }
        si_syms = {
            row[0] for row in
            conn.execute(
                "SELECT DISTINCT symbol FROM short_interest WHERE short_interest > 0"
            ).fetchall()
        }
        fetch_failed_retry = sorted(fetch_failed & si_syms)[:1000]
        seen: set[str] = set()
        missing: list[str] = []
        for s in symbols:
            if s not in cached or s in etf_no_float:
                seen.add(s); missing.append(s)
        for s in fetch_failed_retry:
            if s in symbols and s not in seen:
                seen.add(s); missing.append(s)

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
                d.get("quote_type"),
                now,
            ))

            if len(rows) >= 200:
                conn.executemany(
                    """INSERT OR REPLACE INTO stock_fundamentals
                       (symbol, short_pct_float, shares_short, short_ratio, float_shares, quote_type, fetched_at)
                       VALUES (?,?,?,?,?,?,?)""",
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
                   (symbol, short_pct_float, shares_short, short_ratio, float_shares, quote_type, fetched_at)
                   VALUES (?,?,?,?,?,?,?)""",
                rows,
            )
            conn.commit()
            saved += len(rows)

    print(f"  ✓ Short Interest 저장: {saved}개")
    return saved


def export_fundamentals_json(conn: sqlite3.Connection) -> int:
    """stock_fundamentals DB → data/short_interest.json 내보내기."""
    import json
    rows = conn.execute(
        """SELECT symbol, short_pct_float, shares_short, short_ratio, quote_type, float_shares
           FROM stock_fundamentals
           WHERE short_pct_float IS NOT NULL
              OR shares_short IS NOT NULL
              OR short_ratio IS NOT NULL
              OR quote_type IS NOT NULL
              OR float_shares IS NOT NULL
           ORDER BY symbol"""
    ).fetchall()
    data = []
    for r in rows:
        entry = {"s": r[0], "p": r[1], "n": r[2], "r": r[3]}
        if r[4]:
            entry["q"] = r[4]
        if r[5]:
            entry["f"] = r[5]   # float_shares
        data.append(entry)
    out = ROOT / "data" / "short_interest.json"
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"  ✓ short_interest.json 저장: {len(data)}개 종목")
    return len(data)


if __name__ == "__main__":
    import argparse
    from collector.database import connect

    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="전체 재조회 (force)")
    parser.add_argument("--export", action="store_true", help="DB → JSON 내보내기만")
    args = parser.parse_args()

    conn = connect(DB_PATH)

    if args.export:
        export_fundamentals_json(conn)
        conn.close()
        raise SystemExit(0)

    syms = [
        r[0] for r in
        conn.execute("SELECT DISTINCT symbol FROM daily_short_volume").fetchall()
    ]

    fetch_fundamentals(conn, syms, force=args.all)
    export_fundamentals_json(conn)
    conn.close()
