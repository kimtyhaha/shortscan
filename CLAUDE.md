# 숏스캔 (ShortScan) — 미국 주식 공매도 현황 사이트

## 프로젝트 목표
한국인 미국주식 투자자 대상 애드센스 수익형 콘텐츠 사이트.
FINRA 공매도 데이터를 매일 수집 → 종목별 페이지 자동 생성 (프로그래매틱 SEO).
타겟 검색어: "테슬라 공매도", "엔비디아 공매도 비율" 등 (구글 메인, 네이버 보조).

## 현재 상태 (스켈레톤 완성)
- [x] FINRA 일별 공매도 거래량 수집기 (`collector/finra_daily.py`) — 무인증 CDN 텍스트 파일
- [x] SQLite 저장 (`collector/database.py`)
- [x] 규칙 기반 자동 해설 생성 (`analyzer/summary.py`)
- [x] 파이프라인 진입점 (`main.py`)
- [ ] 격주 Short Interest 수집 (FINRA Query API — 무료 키 발급 필요, https://developer.finra.org)
- [ ] 한국어 종목명 매핑 테이블 (TSLA→테슬라 등, 주요 500종목)
- [ ] 랭킹 산출 (급증 TOP 20, 숏스퀴즈 후보)
- [ ] 정적 사이트 생성 (Next.js 또는 Astro) — 시안은 아래 "디자인" 참고
- [ ] GitHub Actions 크론 (미 동부 기준 매일 저녁 — FINRA 파일은 당일 장 마감 후 게시)
- [ ] Cloudflare Pages 배포

## 데이터 소스
1. **일별 공매도 거래량 (메인, 무인증)**
   - URL: `https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt`
   - 파이프(|) 구분: `Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market`
   - CNMS = 통합 NMS 파일. 휴장일은 파일 없음(404) → 정상 처리됨
2. **격주 Short Interest (잔고)** — FINRA Query API, 키 필요. 공시 지연 ~2주
3. **SEC Fails-to-Deliver** — 추후 확장

## ⚠️ 데이터 표기 주의사항 (법적/신뢰성)
- FINRA 일별 데이터는 **장외(off-exchange) 보고 거래만** 포함. 거래소 직접 체결분 제외.
  → 사이트에 반드시 "FINRA 보고 기준 공매도 비율" 명시. "전체 시장 공매도 비율" 아님
- Short Interest는 격주 공시 + ~2주 지연 → "실시간" 표현 금지, "최신 공시 기준" 사용
- 투자 권유 아님 면책 문구 필수 (애드센스 + 한국 자본시장법 고려)

## 디자인 (시안 확정됨)
- 메인: 날짜 헤더 + 지표 카드 2개(시장 평균 비율, 급증 종목 수) + 급증 TOP 5 리스트(빨간 변화량 배지) + 하단 광고
- 종목 상세 (`/stock/{TICKER}`): 지표 카드 4개(일별 비율, 잔고 비율, Days to Cover, 전일 대비) + 10거래일 막대 차트(최근일 빨강 강조) + 파란 박스 "자동 분석 요약" + 인아티클 광고
- 톤: 플랫, 흰 배경, 미니멀 보더. 빨강=공매도 증가, 파랑=정보

## 자동 해설 (`analyzer/summary.py`)
규칙 기반 문장 생성. 애드센스 승인(콘텐츠성)과 체류시간의 핵심.
현재 규칙: 연속 상승/하락 일수, 10일 평균 대비 괴리, 급증/급감 임계값.
확장 아이디어: 문장 템플릿 3~5종 랜덤 → 페이지 간 중복 텍스트 감소 (SEO).

## 실행
```bash
pip install -r requirements.txt
python main.py                  # 최근 영업일 수집 + 분석
python main.py --date 20260610  # 특정일
python main.py --backfill 30    # 최근 30일 백필 (초기 1회 필수 — 차트/평균 계산용)
```

## 코딩 컨벤션
- 주석/로그 한국어, 코드 영어
- 외부 요청은 requests + 재시도/타임아웃, User-Agent 명시
- DB 스키마 변경 시 database.py의 SCHEMA_VERSION 올리기
