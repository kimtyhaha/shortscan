"""FINRA 일별 공매도 거래량 수집기.

무인증 CDN 텍스트 파일 사용:
https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt
형식(파이프 구분): Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

logger = logging.getLogger(__name__)

CDN_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{yyyymmdd}.txt"
HEADERS = {"User-Agent": "ShortScan/0.1 (data collector; contact: admin@example.com)"}
TIMEOUT = 30


@dataclass
class ShortVolumeRecord:
    trade_date: str        # YYYY-MM-DD
    symbol: str
    short_volume: int
    short_exempt_volume: int
    total_volume: int
    market: str

    @property
    def short_ratio(self) -> float:
        """FINRA 보고 기준 공매도 비율(%). 장외 보고분 기준이므로 전체 시장 비율 아님."""
        if self.total_volume <= 0:
            return 0.0
        return round(self.short_volume / self.total_volume * 100, 2)


def fetch_daily_file(target: date, session: requests.Session | None = None) -> str | None:
    """해당 일자 CNMS 파일 원문 다운로드. 휴장일(404)은 None 반환."""
    sess = session or requests.Session()
    url = CDN_URL.format(yyyymmdd=target.strftime("%Y%m%d"))
    logger.info("FINRA 파일 요청: %s", url)
    resp = sess.get(url, headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code in (404, 403):
        logger.info("파일 없음(휴장일 또는 미공개): %s", target)
        return None
    resp.raise_for_status()
    return resp.text


def parse_daily_file(raw: str) -> list[ShortVolumeRecord]:
    """파이프 구분 텍스트 → 레코드 리스트. 헤더/푸터/불량 행은 건너뜀."""
    records: list[ShortVolumeRecord] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("Date|"):
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        try:
            d = datetime.strptime(parts[0], "%Y%m%d").date().isoformat()
            records.append(ShortVolumeRecord(
                trade_date=d,
                symbol=parts[1].strip().upper(),
                short_volume=round(float(parts[2])),
                short_exempt_volume=round(float(parts[3])),
                total_volume=round(float(parts[4])),
                market=parts[5].strip(),
            ))
        except (ValueError, IndexError):
            logger.debug("파싱 불가 행 건너뜀: %s", line[:80])
    logger.info("파싱 완료: %d개 레코드", len(records))
    return records


def collect(target: date) -> list[ShortVolumeRecord]:
    """지정일 데이터 수집. 휴장일이면 빈 리스트."""
    raw = fetch_daily_file(target)
    if raw is None:
        return []
    return parse_daily_file(raw)


def latest_business_day(today: date | None = None) -> date:
    """가장 최근 영업일 추정(주말 제외, 미국 공휴일은 404로 자연 처리)."""
    d = today or date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
