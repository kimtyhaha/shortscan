"""OG 이미지 생성기 (1200×630 PNG).

사용법:
    python site/og_image.py                  # 메인 og.png
    python site/og_image.py --stock TSLA 54.7 테슬라
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as _pil_err:
    raise ImportError("pillow 없음 — pip install pillow") from _pil_err

W, H = 1200, 630
OUT_DIR = Path(__file__).parent / "out"

# 폰트 후보 (로컬 macOS → Actions Ubuntu 순)
_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_text_centered(draw: ImageDraw.ImageDraw, y: int, text: str,
                         font: ImageFont.FreeTypeFont, color: str) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (W - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), text, font=font, fill=color)


def build_main_og(avg_ratio: float, surge_count: int, trade_date: str) -> Path:
    img  = Image.new("RGB", (W, H), "#ffffff")
    draw = ImageDraw.Draw(img)

    # 상단 빨간 바
    draw.rectangle([0, 0, W, 8], fill="#e53935")

    # 배경 그라디언트 효과 (연한 회색 하단)
    draw.rectangle([0, 8, W, H], fill="#f9fafb")
    draw.rectangle([0, 8, W, 120], fill="#ffffff")

    # 브랜드
    f_brand = _font(56)
    f_sub   = _font(30)
    f_big   = _font(80)
    f_label = _font(22)
    f_small = _font(20)

    draw.text((72, 40), "숏스캔", font=f_brand, fill="#e53935")
    draw.text((220, 56), "ShortScan", font=_font(28), fill="#9ca3af")

    # 구분선
    draw.rectangle([72, 118, W - 72, 121], fill="#e5e7eb")

    # 부제목
    draw.text((72, 140), "미국 주식 공매도 비율 현황", font=f_sub, fill="#374151")
    draw.text((72, 182), f"FINRA 보고 기준  ·  {trade_date}", font=f_label, fill="#9ca3af")

    # 지표 카드 2개
    for i, (val, lbl, col) in enumerate([
        (f"{avg_ratio}%", "시장 평균 공매도 비율", "#e53935"),
        (f"{surge_count}종목",  "급증 종목 수 (+3%p↑)",   "#1976d2"),
    ]):
        cx = 72 + i * 530
        draw.rectangle([cx, 240, cx + 490, 420], fill="#ffffff", outline="#e5e7eb", width=1)
        draw.text((cx + 30, 268), val, font=f_big, fill=col)
        draw.text((cx + 30, 370), lbl, font=f_label, fill="#6b7280")

    # 하단 URL
    draw.rectangle([0, H - 60, W, H], fill="#ffffff")
    draw.text((72, H - 44), "shortscan.pages.dev", font=f_small, fill="#9ca3af")
    draw.text((W - 350, H - 44), "FINRA 공매도 데이터 무료 제공", font=f_small, fill="#d1d5db")

    out = OUT_DIR / "og.png"
    img.save(out, "PNG", optimize=True)
    print(f"  ✓ og.png  ({out.stat().st_size // 1024}KB)")
    return out


def build_stock_og(symbol: str, name_kr: str, ratio: float, change: float) -> Path:
    img  = Image.new("RGB", (W, H), "#ffffff")
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 8], fill="#e53935")
    draw.rectangle([0, 8, W, H], fill="#f9fafb")
    draw.rectangle([0, 8, W, 120], fill="#ffffff")

    f_brand = _font(44)
    f_sym   = _font(72)
    f_name  = _font(36)
    f_ratio = _font(100)
    f_label = _font(24)
    f_small = _font(20)

    draw.text((72, 44), "숏스캔", font=f_brand, fill="#e53935")

    draw.rectangle([72, 118, W - 72, 121], fill="#e5e7eb")

    draw.text((72, 148), symbol, font=f_sym, fill="#111827")
    draw.text((72, 232), name_kr, font=f_name, fill="#6b7280")

    # 비율
    col = "#e53935" if ratio >= 50 else "#1976d2"
    draw.text((72, 290), f"{ratio}%", font=f_ratio, fill=col)
    draw.text((72, 408), "공매도 비율 (FINRA 보고 기준)", font=f_label, fill="#9ca3af")

    # 변화량
    chg_str = f"{change:+.1f}%p"
    chg_col = "#e53935" if change >= 0 else "#1976d2"
    draw.rectangle([680, 290, 1130, 420], fill="#f3f4f6", outline="#e5e7eb")
    draw.text((710, 310), "전일 대비", font=f_label, fill="#9ca3af")
    draw.text((710, 344), chg_str, font=_font(60), fill=chg_col)

    draw.rectangle([0, H - 60, W, H], fill="#ffffff")
    draw.text((72, H - 44), "shortscan.pages.dev", font=f_small, fill="#9ca3af")

    safe = symbol.replace("/", "-")
    out = OUT_DIR / "og" / f"{safe}.png"
    out.parent.mkdir(exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", nargs=3, metavar=("SYM", "RATIO", "NAME"))
    parser.add_argument("--avg",   type=float, default=45.0)
    parser.add_argument("--surge", type=int,   default=0)
    parser.add_argument("--date",  default="2026-06-10")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    if args.stock:
        sym, ratio, name = args.stock
        build_stock_og(sym, name, float(ratio), 0)
    else:
        build_main_og(args.avg, args.surge, args.date)
