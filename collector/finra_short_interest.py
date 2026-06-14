"""FINRA Consolidated Short Interest 수집기.

격주(~월 2회) 공매도 잔고 데이터 수집. 무인증 공개 API.
API: https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest

사용법:
    python -m collector.finra_short_interest          # 최근 2개 결산일
    python -m collector.finra_short_interest --dates 2026-05-15 2026-04-30
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT    = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "shortscan.db"

API_URL   = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
PAGE_SIZE = 5000
HEADERS   = {
    "User-Agent": "ShortscanBot/1.0 (shortscan.cc)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def _post(body: dict, timeout: int = 60) -> list[dict]:
    r = requests.post(API_URL, json=body, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def find_latest_settlement_dates(n: int = 2) -> list[str]:
    """FINRA API에서 최근 N개 결산일 탐색.

    결산일은 격주이므로 최근 3개월 범위에서 찾는다.
    """
    today = datetime.utcnow().date()
    start = (today - timedelta(days=90)).isoformat()
    end   = today.isoformat()

    data = _post({
        "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": start, "endDate": end}],
        "limit": 500,
    })
    if not data:
        return []

    dates = sorted({row["settlementDate"] for row in data}, reverse=True)
    return dates[:n]


def fetch_for_date(settlement_date: str) -> list[dict]:
    """특정 결산일의 전체 종목 공매잔고 수집 (페이지네이션)."""
    records: list[dict] = []
    offset = 0

    while True:
        batch = _post({
            "compareFilters": [
                {"fieldName": "settlementDate", "compareType": "EQUAL", "fieldValue": settlement_date}
            ],
            "limit": PAGE_SIZE,
            "offset": offset,
        })
        if not batch:
            break
        records.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.3)

    return records


def upsert(conn: sqlite3.Connection, records: list[dict]) -> int:
    rows = [
        (
            r["settlementDate"],
            r["symbolCode"],
            r.get("currentShortPositionQuantity") or 0,
            r.get("averageDailyVolumeQuantity"),
            r.get("daysToCoverQuantity"),
        )
        for r in records
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO short_interest
           (settlement_date, symbol, short_interest, avg_daily_volume, days_to_cover)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def collect(conn: sqlite3.Connection, dates: list[str] | None = None) -> None:
    """수집 메인 함수.

    dates: 수집할 결산일 목록. None이면 최근 2개 자동 탐색.
    """
    if dates is None:
        print("  → 최근 결산일 탐색 중...")
        dates = find_latest_settlement_dates(n=2)
        if not dates:
            print("  ⚠ 최근 90일 내 결산일 없음")
            return
        print(f"  → 결산일: {dates}")

    # 이미 DB에 있는 결산일 스킵
    cached = {
        row[0] for row in
        conn.execute("SELECT DISTINCT settlement_date FROM short_interest").fetchall()
    }

    for d in dates:
        if d in cached:
            print(f"  ✓ {d}: 이미 수집됨, 스킵")
            continue
        print(f"  → {d} 수집 중...")
        records = fetch_for_date(d)
        if records:
            cnt = upsert(conn, records)
            print(f"  ✓ {d}: {cnt}개 저장")
        else:
            print(f"  ⚠ {d}: 데이터 없음")


if __name__ == "__main__":
    import argparse
    from collector.database import connect

    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs="+", help="수집할 결산일 (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="이미 있는 날짜도 재수집")
    args = parser.parse_args()

    conn = connect(DB_PATH)
    if args.force and args.dates:
        # force: 캐시 무시하고 지정 날짜 수집
        for d in args.dates:
            print(f"  → {d} 강제 재수집...")
            records = fetch_for_date(d)
            if records:
                cnt = upsert(conn, records)
                print(f"  ✓ {d}: {cnt}개 저장")
    else:
        collect(conn, dates=args.dates)
    conn.close()
