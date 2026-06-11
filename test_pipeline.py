"""오프라인 동작 검증: 샘플 데이터로 파싱→저장→랭킹→해설 전체 흐름 테스트."""
from collector import database, finra_daily
from analyzer.summary import generate_summary

SAMPLE = """Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260609|TSLA|2300000|1000|10000000|B,Q,N
20260609|NVDA|1500000|500|12000000|B,Q,N
20260610|TSLA|3140000|1200|10000000|B,Q,N
20260610|NVDA|1600000|600|12000000|B,Q,N
"""

records = finra_daily.parse_daily_file(SAMPLE)
assert len(records) == 4, records
assert records[2].short_ratio == 31.4

conn = database.connect(":memory:")
assert database.upsert_daily(conn, records) == 4

series = database.recent_ratios(conn, "TSLA")
assert series == [("2026-06-09", 23.0), ("2026-06-10", 31.4)], series

surge = database.top_ratio_surge(conn, "2026-06-10", limit=5)
assert surge[0]["symbol"] == "TSLA" and surge[0]["change"] == 8.4, surge

print(generate_summary("테슬라", [("d1", 21.0), ("d2", 22.5), ("d3", 23.0), ("d4", 31.4)]))
print("모든 테스트 통과")
