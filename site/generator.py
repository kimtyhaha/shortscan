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
                symbol: str, trade_date: str) -> bool:
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

    name_kr = get_kr_name(symbol)
    summary = generate_summary(name_kr, series)

    # 차트 레이블: MM/DD 형식
    labels = [d[5:] for d in dates]

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
    )

    safe_sym = symbol.replace("/", "-").replace("\\", "-")
    out_path = OUT_DIR / "stock" / f"{safe_sym}.html"
    out_path.write_text(html, encoding="utf-8")
    return True


SITE_URL = "https://shortscan.pages.dev"


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

def build_robots() -> None:
    content = f"""User-agent: *
Allow: /

Sitemap: {SITE_URL}/sitemap.xml
"""
    (OUT_DIR / "robots.txt").write_text(content, encoding="utf-8")
    print(f"  ✓ robots.txt")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true", help="생성 후 브라우저 열기")
    parser.add_argument("--tickers", nargs="+", help="특정 종목만 생성")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "stock").mkdir(exist_ok=True)

    conn = connect(DB_PATH)
    env  = make_env()

    trade_date = latest_trade_date(conn)
    if not trade_date:
        print("DB에 데이터 없음. main.py --backfill 30 먼저 실행하세요.")
        return

    print(f"\n🔨 사이트 생성 중... (기준일: {trade_date})")

    # 메인 페이지
    build_index(conn, env, trade_date)

    # 종목 상세 — 지정 종목 or DB 전체 종목
    if args.tickers:
        symbols = [t.upper() for t in args.tickers]
    else:
        cur = conn.execute(
            "SELECT DISTINCT symbol FROM daily_short_volume WHERE trade_date = ? ORDER BY symbol",
            (trade_date,),
        )
        symbols = [row[0] for row in cur.fetchall()]

    ok = skip = 0
    for sym in symbols:
        if build_stock(conn, env, sym, trade_date):
            ok += 1
        else:
            skip += 1

    conn.close()

    print(f"  ✓ 종목 페이지 {ok}개 생성 (데이터 없음 {skip}개 건너뜀)")

    # sitemap.xml + robots.txt
    built_symbols = [s for s in symbols if build_stock.__doc__ or True]  # 생성된 종목 목록
    build_sitemap(symbols, trade_date)
    build_robots()

    print(f"\n✅ 완료 → {OUT_DIR}/index.html\n")

    if args.open:
        webbrowser.open(f"file://{(OUT_DIR / 'index.html').resolve()}")


if __name__ == "__main__":
    main()
