"""숏스캔 데이터 파이프라인 진입점.

사용법:
    python main.py                  # 최근 영업일 수집
    python main.py --date 20260610  # 특정일 수집
    python main.py --backfill 30    # 최근 30일 백필
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from analyzer.summary import generate_summary
from analyzer.ranking import (
    surge_ranking, high_ratio_ranking, squeeze_candidates,
    latest_trade_date, print_ranking,
)
from collector import database, finra_daily
from collector.kr_names import get_kr_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("main")

DB_PATH = Path(__file__).parent / "data" / "shortscan.db"
WATCH_SYMBOLS = ["TSLA", "NVDA", "AAPL", "PLTR", "SOFI"]  # 해설 미리보기용


def run_for_date(conn, target: date) -> int:
    records = finra_daily.collect(target)
    if not records:
        logger.info("%s: 데이터 없음(휴장일)", target)
        return 0
    n = database.upsert_daily(conn, records)
    logger.info("%s: %d개 종목 저장", target, n)
    return n


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYYMMDD")
    parser.add_argument("--backfill", type=int, help="최근 N일 백필")
    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = database.connect(DB_PATH)

    if args.backfill:
        end = finra_daily.latest_business_day()
        for i in range(args.backfill):
            run_for_date(conn, end - timedelta(days=i))
    elif args.date:
        run_for_date(conn, datetime.strptime(args.date, "%Y%m%d").date())
    else:
        run_for_date(conn, finra_daily.latest_business_day())

    # ── 랭킹 산출 ──────────────────────────────────────────────────────────
    date_str = latest_trade_date(conn)
    if date_str:
        print(f"\n📅 기준일: {date_str}  (FINRA 보고 기준, 장외 거래 포함)")

        surge  = surge_ranking(conn, date_str, limit=20)
        high   = high_ratio_ranking(conn, date_str, limit=20)
        squeeze = squeeze_candidates(conn, date_str, limit=20)

        print_ranking(surge,   "📈 공매도 비율 급증 TOP 20  (전일 대비 +3%p 이상)")
        print_ranking(high,    "🔴 공매도 비율 절대값 TOP 20  (50% 이상)")
        print_ranking(squeeze, "🔥 숏스퀴즈 후보  (비율 40%↑ + 전일 대비 하락)")

    # ── 자동 해설 미리보기 ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  📝 종목별 자동 해설 미리보기")
    print(f"{'='*60}")
    for sym in WATCH_SYMBOLS:
        series = database.recent_ratios(conn, sym, days=10)
        if series:
            name = get_kr_name(sym)
            print(f"\n  [{sym} / {name}]")
            print(f"  {generate_summary(name, series)}")

    conn.close()


if __name__ == "__main__":
    main()
