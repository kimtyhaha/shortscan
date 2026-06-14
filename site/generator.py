"""정적 사이트 생성기.

사용법:
    python site/generator.py              # DB → out/ 폴더에 HTML 생성
    python site/generator.py --open       # 생성 후 브라우저 열기
    python site/generator.py --tickers TSLA NVDA  # 특정 종목만 재생성
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import webbrowser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# 경로 설정
ROOT    = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "shortscan.db"
OUT_DIR = Path(__file__).parent / "out"
TMPL_DIR = Path(__file__).parent / "templates"

sys.path.insert(0, str(ROOT))
from analyzer.ranking import (
    surge_ranking, high_ratio_ranking, squeeze_candidates, latest_trade_date,
)
from analyzer.summary import generate_summary
from collector.database import connect, recent_ratios
from collector.fetch_names import fetch_missing, fetch_fundamentals, import_fundamentals_json
from collector.kr_names import get_kr_name


# ── Jinja2 필터 ──────────────────────────────────────────────────────────────

def vol_fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)

def format_num(n: int) -> str:
    return f"{n:,}"


def make_env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TMPL_DIR)), autoescape=True)
    env.filters["vol_fmt"]    = vol_fmt
    env.filters["format_num"] = format_num
    env.filters["tojson"]     = json.dumps
    env.filters["format"]     = lambda fmt, val: fmt % val
    return env


# ── 메인 페이지 ───────────────────────────────────────────────────────────────

def build_index(conn: sqlite3.Connection, env: Environment, trade_date: str) -> None:
    surge_all  = surge_ranking(conn, trade_date, limit=20, min_total_volume=1_000_000, min_change=3.0)
    surge_top5 = surge_all[:5]
    high_all   = high_ratio_ranking(conn, trade_date, limit=20, min_total_volume=1_000_000)
    squeeze    = squeeze_candidates(conn, trade_date, limit=20, min_total_volume=1_000_000)

    # 시장 평균 비율
    cur = conn.execute(
        """SELECT AVG(CAST(short_volume AS REAL) / NULLIF(total_volume,0) * 100),
                  COUNT(*)
           FROM daily_short_volume
           WHERE trade_date = ? AND total_volume > 0""",
        (trade_date,),
    )
    avg_r, total_count = cur.fetchone()
    avg_ratio = round(avg_r or 0, 1)

    # 전일 평균 비율
    cur2 = conn.execute(
        """SELECT AVG(CAST(short_volume AS REAL) / NULLIF(total_volume,0) * 100)
           FROM daily_short_volume
           WHERE trade_date = (
               SELECT MAX(trade_date) FROM daily_short_volume WHERE trade_date < ?
           ) AND total_volume > 0""",
        (trade_date,),
    )
    prev_avg = cur2.fetchone()[0] or 0
    change = avg_ratio - round(prev_avg, 1)
    avg_change = f"{change:+.1f}%p" if change else "—"

    surge_count = len([r for r in surge_all if r.change is not None and r.change >= 5])

    tmpl = env.get_template("index.html")
    html = tmpl.render(
        trade_date=trade_date,
        avg_ratio=avg_ratio,
        avg_change=avg_change,
        surge_count=surge_count,
        squeeze_count=len(squeeze),
        total_count=total_count or 0,
        surge_top5=surge_top5,
        surge_all=surge_all,
        squeeze=squeeze,
    )
    (OUT_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"  ✓ index.html")

    # 랭킹 페이지 (full)
    tmpl_rank = env.get_template("ranking.html") if (TMPL_DIR / "ranking.html").exists() else None
    if tmpl_rank:
        html_r = tmpl_rank.render(
            trade_date=trade_date,
            surge_all=surge_all,
            high_all=high_all,
            squeeze=squeeze,
        )
        (OUT_DIR / "ranking.html").write_text(html_r, encoding="utf-8")
        print(f"  ✓ ranking.html")


# ── 종목 상세 페이지 ──────────────────────────────────────────────────────────

def build_stock(conn: sqlite3.Connection, env: Environment,
                symbol: str, trade_date: str, gen_og: bool = False) -> bool:
    series = recent_ratios(conn, symbol, days=10)
    if not series:
        return False

    ratios = [r for _, r in series]
    dates  = [d for d, _ in series]

    # 거래량
    vols = []
    for d, _ in series:
        cur = conn.execute(
            "SELECT total_volume FROM daily_short_volume WHERE trade_date=? AND symbol=?",
            (d, symbol),
        )
        row = cur.fetchone()
        vols.append(row[0] if row else 0)

    latest_ratio = ratios[-1]
    prev_ratio   = ratios[-2] if len(ratios) >= 2 else latest_ratio
    change       = round(latest_ratio - prev_ratio, 1)
    avg_ratio    = round(sum(ratios) / len(ratios), 1)
    latest_vol   = vol_fmt(vols[-1]) if vols else "—"

    # 테이블 행
    table_rows = []
    for i, (d, r) in enumerate(series):
        prev_r = ratios[i - 1] if i > 0 else r
        table_rows.append({
            "date": d,
            "ratio": r,
            "chg": round(r - prev_r, 1),
            "vol": vol_fmt(vols[i]) if i < len(vols) else "—",
        })
    table_rows.reverse()

    name_kr = get_kr_name(symbol, conn)
    summary = generate_summary(name_kr, series)

    # Short Interest: yfinance(stock_fundamentals) 우선, 없으면 FINRA(short_interest) 사용
    fi = conn.execute(
        "SELECT short_pct_float, shares_short, short_ratio, quote_type, float_shares FROM stock_fundamentals WHERE symbol=?",
        (symbol,),
    ).fetchone()
    short_pct    = round(fi[0] * 100, 1) if fi and fi[0] else None
    shares_short = fi[1] if fi else None
    short_ratio  = round(fi[2], 1) if fi and fi[2] else None
    is_etf       = bool(fi and fi[3] == "ETF")
    float_shares = fi[4] if fi else None

    # yfinance 데이터 없으면 FINRA consolidatedShortInterest 사용
    if shares_short is None:
        si = conn.execute(
            """SELECT short_interest, avg_daily_volume, days_to_cover
               FROM short_interest WHERE symbol=?
               ORDER BY settlement_date DESC LIMIT 1""",
            (symbol,),
        ).fetchone()
        if si and si[0]:
            shares_short = si[0]
            if short_ratio is None and si[2]:
                short_ratio = round(si[2], 1)

    # Float% 없으면 shares_short / float_shares 로 계산 (ETF 포함)
    if short_pct is None and shares_short and float_shares and float_shares > 0:
        short_pct = round(shares_short / float_shares * 100, 1)

    # 차트 레이블: MM/DD 형식
    labels = [d[5:] for d in dates]

    safe_sym = symbol.replace("/", "-").replace("\\", "-")

    # 종목별 OG 이미지 (gen_og=True인 상위 종목만)
    if gen_og:
        try:
            from og_image import build_stock_og
            og_path = OUT_DIR / "og" / f"{safe_sym}.png"
            if not og_path.exists():
                build_stock_og(symbol, name_kr, latest_ratio, change)
        except Exception:
            pass

    tmpl = env.get_template("stock.html")
    html = tmpl.render(
        symbol=symbol,
        name_kr=name_kr,
        trade_date=trade_date,
        latest_ratio=latest_ratio,
        prev_ratio=prev_ratio,
        change=change,
        avg_ratio=avg_ratio,
        latest_vol=latest_vol,
        series=series,
        labels=labels,
        values=ratios,
        table_rows=table_rows,
        summary=summary,
        short_pct=short_pct,
        shares_short=shares_short,
        short_ratio=short_ratio,
        is_etf=is_etf,
        has_og=gen_og,
    )

    out_path = OUT_DIR / "stock" / f"{safe_sym}.html"
    out_path.write_text(html, encoding="utf-8")
    return True


SITE_URL = "https://shortscan.cc"


# ── 검색 인덱스 ───────────────────────────────────────────────────────────────

def build_search_index(conn: sqlite3.Connection, trade_date: str) -> None:
    """전체 종목 검색 인덱스 JSON 생성 — 티커·한국어명·비율 포함."""
    cur = conn.execute(
        """SELECT symbol,
                  ROUND(CAST(short_volume AS REAL) / NULLIF(total_volume,0) * 100, 1)
           FROM daily_short_volume
           WHERE trade_date = ? AND total_volume > 0
           ORDER BY symbol""",
        (trade_date,),
    )
    index = [
        {"s": sym, "n": get_kr_name(sym, conn), "r": ratio}
        for sym, ratio in cur.fetchall()
    ]
    (OUT_DIR / "search-index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  ✓ search-index.json  ({len(index)}개 종목)")


# ── sitemap.xml ───────────────────────────────────────────────────────────────

def build_sitemap(symbols: list[str], trade_date: str) -> None:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']

    # 메인 페이지
    lines += [
        "  <url>",
        f"    <loc>{SITE_URL}/</loc>",
        f"    <lastmod>{trade_date}</lastmod>",
        "    <changefreq>daily</changefreq>",
        "    <priority>1.0</priority>",
        "  </url>",
    ]

    # 랭킹 페이지
    lines += [
        "  <url>",
        f"    <loc>{SITE_URL}/ranking.html</loc>",
        f"    <lastmod>{trade_date}</lastmod>",
        "    <changefreq>daily</changefreq>",
        "    <priority>0.9</priority>",
        "  </url>",
    ]

    # 종목 상세 페이지
    for sym in symbols:
        safe = sym.replace("/", "-").replace("\\", "-")
        lines += [
            "  <url>",
            f"    <loc>{SITE_URL}/stock/{safe}.html</loc>",
            f"    <lastmod>{trade_date}</lastmod>",
            "    <changefreq>daily</changefreq>",
            "    <priority>0.7</priority>",
            "  </url>",
        ]

    lines.append("</urlset>")
    (OUT_DIR / "sitemap.xml").write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✓ sitemap.xml  ({len(symbols) + 2}개 URL)")


# ── robots.txt ────────────────────────────────────────────────────────────────

def build_rss(conn: sqlite3.Connection, trade_date: str) -> None:
    """공매도 급증 TOP 20 RSS 피드 생성."""
    from analyzer.ranking import surge_ranking
    surge = surge_ranking(conn, trade_date, limit=20, min_total_volume=1_000_000, min_change=3.0)

    items = []
    for r in surge:
        safe = r.symbol.replace("/", "-").replace("\\", "-")
        name = get_kr_name(r.symbol, conn)
        chg  = f"{r.change:+.1f}" if r.change is not None else "—"
        items.append(f"""  <item>
    <title>{name} ({r.symbol}) 공매도 {r.ratio}% ({chg}%p)</title>
    <link>{SITE_URL}/stock/{safe}.html</link>
    <guid>{SITE_URL}/stock/{safe}.html#{trade_date}</guid>
    <pubDate>{trade_date}T00:00:00+00:00</pubDate>
    <description>{name} ({r.symbol}) 공매도 비율 {r.ratio}%, 전일 대비 {chg}%p. FINRA 보고 기준.</description>
  </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>숏스캔 — 공매도 급증 종목</title>
    <link>{SITE_URL}/</link>
    <description>FINRA 기준 미국 주식 공매도 급증 종목 TOP 20. 매일 업데이트.</description>
    <language>ko</language>
    <lastBuildDate>{trade_date}T00:00:00+00:00</lastBuildDate>
    <atom:link href="{SITE_URL}/rss.xml" rel="self" type="application/rss+xml"/>
{chr(10).join(items)}
  </channel>
</rss>"""
    (OUT_DIR / "rss.xml").write_text(rss, encoding="utf-8")
    print(f"  ✓ rss.xml  ({len(items)}개 항목)")


def build_robots() -> None:
    content = f"""User-agent: *
Allow: /

Sitemap: {SITE_URL}/sitemap.xml
"""
    (OUT_DIR / "robots.txt").write_text(content, encoding="utf-8")
    print(f"  ✓ robots.txt")


def build_redirects(symbols: list[str]) -> None:
    """Cloudflare Pages Pretty URLs handles /stock/X → X.html automatically.
    _redirects only needed for non-stock paths; keep file empty to avoid conflicts."""
    (OUT_DIR / "_redirects").write_text("", encoding="utf-8")
    print(f"  ✓ _redirects  (Cloudflare Pretty URLs 사용, 별도 룰 없음)")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true", help="생성 후 브라우저 열기")
    parser.add_argument("--tickers", nargs="+", help="특정 종목만 생성")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "stock").mkdir(exist_ok=True)
    (OUT_DIR / "js").mkdir(exist_ok=True)

    # Chart.js 로컬 복사 (CDN 의존 제거)
    chartjs_local = OUT_DIR / "js" / "chart.min.js"
    if not chartjs_local.exists():
        import urllib.request
        url = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
        print(f"  → Chart.js 다운로드 중...")
        urllib.request.urlretrieve(url, chartjs_local)
        print(f"  ✓ Chart.js 저장 ({chartjs_local.stat().st_size // 1024}KB)")

    conn = connect(DB_PATH)
    env  = make_env()

    trade_date = latest_trade_date(conn)
    if not trade_date:
        print("DB에 데이터 없음. main.py --backfill 30 먼저 실행하세요.")
        return

    # 종목 목록 먼저 확보
    if args.tickers:
        symbols = [t.upper() for t in args.tickers]
    else:
        cur = conn.execute(
            "SELECT DISTINCT symbol FROM daily_short_volume WHERE trade_date = ? ORDER BY symbol",
            (trade_date,),
        )
        symbols = [row[0] for row in cur.fetchall()]

    # 하드코딩에 없는 종목 yfinance로 이름 조회 (캐시 미스만)
    from collector.kr_names import KR_NAMES
    need_fetch = [s for s in symbols if s not in KR_NAMES]
    if need_fetch:
        fetch_missing(conn, need_fetch)

    # Short Interest: JSON 먼저 import → 나머지 yfinance 조회
    import_fundamentals_json(conn)
    fetch_fundamentals(conn, symbols)

    print(f"\n🔨 사이트 생성 중... (기준일: {trade_date})")

    # OG 이미지
    try:
        from og_image import build_main_og, build_stock_og
        cur = conn.execute(
            """SELECT AVG(CAST(short_volume AS REAL)/NULLIF(total_volume,0)*100),
                      COUNT(*) FROM daily_short_volume WHERE trade_date=? AND total_volume>0""",
            (trade_date,),
        )
        avg_r, _ = cur.fetchone()
        surge_cur = conn.execute(
            """WITH r AS (SELECT symbol, trade_date, total_volume,
                      CAST(short_volume AS REAL)/NULLIF(total_volume,0)*100 AS ratio,
                      LAG(CAST(short_volume AS REAL)/NULLIF(total_volume,0)*100)
                          OVER (PARTITION BY symbol ORDER BY trade_date) AS prev
                  FROM daily_short_volume)
               SELECT COUNT(*) FROM r WHERE trade_date=? AND prev IS NOT NULL AND (ratio-prev)>=3
                 AND total_volume>=1000000""",
            (trade_date,),
        )
        surge_n = (surge_cur.fetchone() or [0])[0]
        (OUT_DIR / "og").mkdir(exist_ok=True)
        build_main_og(round(avg_r or 0, 1), surge_n, trade_date)
        _og_available = True
    except Exception as e:
        print(f"  ⚠ OG 이미지 생성 실패: {e}")
        _og_available = False

    # OG 이미지 생성 대상: 거래량 상위 500 (Cloudflare Pages 20,000파일 제한)
    top500_cur = conn.execute(
        """SELECT symbol FROM daily_short_volume
           WHERE trade_date = ? AND total_volume > 0
           ORDER BY total_volume DESC LIMIT 500""",
        (trade_date,),
    )
    og_symbols = {row[0] for row in top500_cur.fetchall()}

    # 메인 페이지
    build_index(conn, env, trade_date)

    ok = skip = 0
    for sym in symbols:
        if build_stock(conn, env, sym, trade_date, gen_og=(sym in og_symbols)):
            ok += 1
        else:
            skip += 1

    print(f"  ✓ 종목 페이지 {ok}개 생성 (데이터 없음 {skip}개 건너뜀)")

    # 검색 인덱스 + sitemap.xml + robots.txt
    build_search_index(conn, trade_date)
    build_rss(conn, trade_date)

    conn.close()

    build_sitemap(symbols, trade_date)
    build_robots()
    build_redirects(symbols)

    print(f"\n✅ 완료 → {OUT_DIR}/index.html\n")

    if args.open:
        webbrowser.open(f"file://{(OUT_DIR / 'index.html').resolve()}")


if __name__ == "__main__":
    main()
