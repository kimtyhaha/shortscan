"""규칙 기반 자동 해설 생성 — 종목 페이지의 '자동 분석 요약' 박스용.

입력: 최근 N거래일 (날짜, 공매도비율%) 시계열
출력: 한국어 해설 문장 (투자 권유 아닌 사실 서술 톤)
"""
from __future__ import annotations

SURGE_THRESHOLD = 5.0      # 전일 대비 %p — 급증 판정
GAP_THRESHOLD = 1.2        # 10일 평균 대비 배율 — 평균 상회 판정


def _streak(ratios: list[float]) -> int:
    """말일 기준 연속 상승(+)/하락(-) 일수."""
    if len(ratios) < 2:
        return 0
    direction = 1 if ratios[-1] > ratios[-2] else -1
    count = 0
    for i in range(len(ratios) - 1, 0, -1):
        diff = ratios[i] - ratios[i - 1]
        if (diff > 0 and direction == 1) or (diff < 0 and direction == -1):
            count += 1
        else:
            break
    return count * direction


def generate_summary(name_kr: str, series: list[tuple[str, float]]) -> str:
    """예: generate_summary('테슬라', [('2026-06-01', 21.3), ...])"""
    if len(series) < 3:
        return f"{name_kr}의 공매도 데이터가 아직 충분히 쌓이지 않았습니다."

    ratios = [r for _, r in series]
    latest = ratios[-1]
    prev = ratios[-2]
    avg = sum(ratios) / len(ratios)
    change = latest - prev
    streak = _streak(ratios)

    parts: list[str] = []

    if streak >= 3:
        parts.append(f"{name_kr}의 공매도 비율은 최근 {streak}거래일 연속 상승했습니다")
    elif streak <= -3:
        parts.append(f"{name_kr}의 공매도 비율은 최근 {-streak}거래일 연속 하락했습니다")
    elif change >= SURGE_THRESHOLD:
        parts.append(f"{name_kr}의 공매도 비율이 전 거래일 대비 {change:.1f}%p 급등했습니다")
    elif change <= -SURGE_THRESHOLD:
        parts.append(f"{name_kr}의 공매도 비율이 전 거래일 대비 {-change:.1f}%p 급락했습니다")
    else:
        parts.append(f"{name_kr}의 공매도 비율은 {latest:.1f}%로 보합권입니다")

    if latest >= avg * GAP_THRESHOLD:
        parts.append(f"{len(ratios)}일 평균({avg:.1f}%)을 크게 웃돌아 단기 하락 베팅이 집중되는 구간입니다")
    elif latest <= avg / GAP_THRESHOLD:
        parts.append(f"{len(ratios)}일 평균({avg:.1f}%)을 밑돌아 공매도 압력이 완화되는 흐름입니다")

    text = ". ".join(parts) + "."
    return text + " 본 수치는 FINRA 보고 기준이며 투자 판단의 참고 자료입니다."
