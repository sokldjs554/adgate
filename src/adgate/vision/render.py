"""``ImageSpec`` → PNG. Pillow 와 번들 글꼴(``data/fonts``)만 쓴다. 같은 사양·같은 Pillow/FreeType 버전이면 같은 그림이다.

설계 원칙:

* **그리지 못하면 오류.** 글꼴에 없는 글자, 박스에 들어가지 않는 글자는 조용히 잘라 내거나 네모로 대체하지 않고 ``RenderError`` 로 알린다.
  (이미지 속 글자가 정답의 일부이므로, 그려지지 않은 글자가 정답에 남아 있으면 평가가 거짓이 된다.)
* 글자 크기는 기본이 *박스에 맞춤*이다. 크기를 명시한 글자(작은 글씨 시험)는 그 크기로 그리고 박스에 안 맞으면 오류다.
* 이 렌더러는 실제 광고 이미지를 흉내 내지 않는다. 글자 읽기·시각 단서 탐지를 시험할 수 있을 만큼의 *합성* 이미지를 만든다.
"""

from __future__ import annotations

import functools
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .spec import ImageSpec, Item, texts

FONT_DIR = Path(__file__).resolve().parent.parent / "data" / "fonts"
LINE = 1.28
MIN_AUTO = 8
RGB = tuple[int, int, int]
Box = tuple[int, int, int, int]

class RenderError(ValueError):
    """이 사양은 그릴 수 없다(글꼴에 없는 글자, 박스에 안 들어가는 글자)."""


@functools.lru_cache(maxsize=1)
def covered_codepoints() -> frozenset[int]:
    """번들 글꼴 부분집합이 *실제로* 그리는 글자(data/fonts/coverage.json = 글꼴의 cmap, 테스트가 일치를 검사한다)."""
    ranges = json.loads((FONT_DIR / "coverage.json").read_text(encoding="utf-8"))
    return frozenset(c for a, b in ranges for c in range(a, b + 1))


def unrenderable(text: str) -> list[str]:
    """글꼴이 그리지 못하는 글자(줄바꿈 제외)."""
    cov = covered_codepoints()
    return sorted({ch for ch in text if ch != "\n" and ord(ch) not in cov})


@functools.lru_cache(maxsize=128)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"GothicA1-{'Bold' if bold else 'Regular'}.subset.ttf"), size)


def rgb(color: str | None, default: str) -> RGB:
    c = (color or default).lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def shade(c: RGB, f: float) -> RGB:
    """f<1 어둡게, f>1 밝게."""
    if f <= 1:
        return int(c[0] * f), int(c[1] * f), int(c[2] * f)
    return tuple(min(255, int(v + (255 - v) * (f - 1))) for v in c)  # type: ignore[return-value]


class _TooWide(Exception):
    """낱말 하나가 줄 너비보다 길다(글자 단위로 쪼개지 않기로 한 경우)."""


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: float, *, allow_break: bool = True) -> list[str]:
    """낱말 단위로 줄을 나눈다. 낱말 하나가 너무 길면 ``allow_break`` 일 때만 글자 단위로 나누고, 아니면 ``_TooWide``."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for token in re.findall(r"\S+\s*", para) or [""]:
            if f.getlength((cur + token).rstrip()) <= max_w:
                cur += token
                continue
            if cur:
                lines.append(cur.rstrip())
                cur = ""
            if not allow_break and f.getlength(token.rstrip()) > max_w:
                raise _TooWide
            if f.getlength(token.rstrip()) <= max_w:
                cur = token
                continue
            for ch in token:
                if f.getlength((cur + ch).rstrip()) <= max_w:
                    cur += ch
                else:
                    lines.append(cur.rstrip())
                    cur = ch
        lines.append(cur.rstrip())
    return lines


def fit(text: str, w: float, h: float, bold: bool, max_size: int = 160) -> tuple[int, list[str]]:
    """박스에 들어가는 가장 큰 글자 크기와 줄. 낱말을 쪼개지 않는 크기를 먼저 찾고, 없을 때만 글자 단위로 쪼갠다("크/림"처럼 세로로 쌓이지 않게)."""
    for allow_break in (False, True):
        for size in range(min(max_size, int(h)), MIN_AUTO - 1, -1):
            f = font(size, bold)
            try:
                lines = wrap(text, f, w, allow_break=allow_break)
            except _TooWide:
                continue
            if len(lines) * size * LINE <= h and all(f.getlength(line) <= w for line in lines):
                return size, lines
    raise RenderError(f"글자 {text[:20]!r}… 가 {int(w)}×{int(h)} 박스에 들어가지 않습니다(박스를 키우거나 글을 줄이세요)")


def _box(item: Item, W: int, H: int) -> Box:
    x, y, w, h = item.box
    return round(x / 100 * W), round(y / 100 * H), round((x + w) / 100 * W), round((y + h) / 100 * H)


def draw_text(
    img: Image.Image, box: Box, text: str, *, size: int | str = "auto", bold: bool = False, align: str = "center", color: RGB = (20, 20, 20),
    opacity: float = 1.0, rotate: float = 0.0, valign: str = "middle", pad: float = 0.0,
) -> None:
    bad = unrenderable(text)
    if bad:
        raise RenderError(f"글꼴에 없는 글자: {''.join(bad)!r}")
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    iw, ih = w - 2 * pad, h - 2 * pad
    if size == "auto":
        s, lines = fit(text, iw, ih, bold)
    else:
        s = int(size)
        lines = wrap(text, font(s, bold), iw)
        if len(lines) * s * LINE > ih + 0.5 or any(font(s, bold).getlength(line) > iw + 0.5 for line in lines):
            raise RenderError(f"글자 {text[:20]!r}… 가 {int(iw)}×{int(ih)} 박스에 크기 {s} 로 들어가지 않습니다")
    f = font(s, bold)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    total = len(lines) * s * LINE
    y = pad + ((ih - total) / 2 if valign == "middle" else 0)
    alpha = int(255 * opacity)
    for line in lines:
        lw = f.getlength(line)
        x = pad + (0 if align == "left" else (iw - lw) if align == "right" else (iw - lw) / 2)
        d.text((x, y), line, font=f, fill=(*color, alpha))
        y += s * LINE
    if rotate:
        layer = layer.rotate(rotate, resample=Image.Resampling.BICUBIC, expand=True)
    img.alpha_composite(layer, (round(x0 + w / 2 - layer.width / 2), round(y0 + h / 2 - layer.height / 2)))


# --------------------------------------------------------------------------- 상품 일러스트
def _label(img: Image.Image, d: ImageDraw.ImageDraw, rect: tuple[float, float, float, float], text: str | None) -> None:
    if not text:
        return
    x0, y0, x1, y1 = (round(v) for v in rect)
    d.rounded_rectangle((x0, y0, x1, y1), radius=max(3, (y1 - y0) // 8), fill=(255, 255, 255, 235))
    draw_text(img, (x0, y0, x1, y1), text, bold=True, color=(30, 30, 30), pad=3)


def _product(img: Image.Image, item: Item, box: Box) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    c = rgb(item.color, "#4f8cff")
    dark, light = shade(c, 0.7), shade(c, 1.25)

    def P(fx: float, fy: float) -> tuple[float, float]:
        return x0 + fx * bw, y0 + fy * bh

    def R(fx0: float, fy0: float, fx1: float, fy1: float) -> tuple[float, float, float, float]:
        return (*P(fx0, fy0), *P(fx1, fy1))

    shape = item.shape or "generic"
    if shape == "bottle":
        d.rounded_rectangle(R(0.25, 0.22, 0.75, 0.98), radius=bw * 0.12, fill=c)
        d.rectangle(R(0.40, 0.08, 0.60, 0.25), fill=shade(c, 0.9))
        d.rectangle(R(0.36, 0.0, 0.64, 0.10), fill=shade(c, 0.55))
        d.rectangle(R(0.29, 0.28, 0.33, 0.92), fill=(*light, 140))
        _label(img, d, R(0.30, 0.47, 0.70, 0.84), item.label)
    elif shape == "box":
        d.polygon([P(0.10, 0.25), P(0.30, 0.08), P(0.90, 0.08), P(0.70, 0.25)], fill=light)
        d.polygon([P(0.70, 0.25), P(0.90, 0.08), P(0.90, 0.78), P(0.70, 0.95)], fill=dark)
        d.rectangle(R(0.10, 0.25, 0.70, 0.95), fill=c)
        _label(img, d, R(0.14, 0.42, 0.66, 0.80), item.label)
    elif shape == "can":
        d.ellipse(R(0.22, 0.80, 0.78, 0.95), fill=dark)
        d.rectangle(R(0.22, 0.12, 0.78, 0.88), fill=c)
        d.ellipse(R(0.22, 0.05, 0.78, 0.19), fill=light)
        d.rectangle(R(0.22, 0.30, 0.78, 0.72), fill=shade(c, 1.1))
        _label(img, d, R(0.26, 0.36, 0.74, 0.68), item.label)
    elif shape == "bag":
        d.arc(R(0.28, 0.02, 0.72, 0.52), 180, 360, fill=dark, width=max(3, int(bw * 0.025)))
        d.polygon([P(0.15, 0.28), P(0.85, 0.28), P(0.95, 0.96), P(0.05, 0.96)], fill=c)
        d.rectangle(R(0.15, 0.28, 0.85, 0.34), fill=dark)
        _label(img, d, R(0.24, 0.50, 0.76, 0.84), item.label)
    elif shape == "phone":
        d.rounded_rectangle(R(0.28, 0.0, 0.72, 1.0), radius=bw * 0.08, fill=(30, 32, 38))
        d.rounded_rectangle(R(0.31, 0.04, 0.69, 0.96), radius=bw * 0.05, fill=c)
        d.ellipse(R(0.46, 0.015, 0.54, 0.035), fill=(30, 32, 38))
        _label(img, d, R(0.34, 0.38, 0.66, 0.62), item.label)
    elif shape == "shoe":
        d.polygon([P(0.06, 0.74), P(0.09, 0.42), P(0.34, 0.32), P(0.55, 0.50), P(0.82, 0.56), P(0.96, 0.74)], fill=c)
        d.rounded_rectangle(R(0.04, 0.72, 0.97, 0.90), radius=bh * 0.05, fill=(245, 245, 245))
        d.line([P(0.30, 0.45), P(0.50, 0.62)], fill=dark, width=max(2, int(bw * 0.012)))
        _label(img, d, R(0.22, 0.52, 0.62, 0.70), item.label)
    elif shape == "tube":
        d.polygon([P(0.32, 0.12), P(0.68, 0.12), P(0.78, 0.94), P(0.22, 0.94)], fill=c)
        d.rectangle(R(0.40, 0.0, 0.60, 0.13), fill=shade(c, 0.55))
        d.rectangle(R(0.22, 0.90, 0.78, 0.97), fill=dark)
        _label(img, d, R(0.34, 0.34, 0.66, 0.76), item.label)
    elif shape == "cup":
        d.ellipse(R(0.70, 0.36, 0.98, 0.72), outline=dark, width=max(4, int(bw * 0.04)))
        d.polygon([P(0.18, 0.22), P(0.82, 0.22), P(0.70, 0.96), P(0.30, 0.96)], fill=c)
        d.rectangle(R(0.18, 0.22, 0.82, 0.28), fill=light)
        _label(img, d, R(0.30, 0.42, 0.70, 0.76), item.label)
    else:
        d.rounded_rectangle(R(0.18, 0.08, 0.82, 0.96), radius=bw * 0.08, fill=c)
        _label(img, d, R(0.26, 0.38, 0.74, 0.72), item.label)


# --------------------------------------------------------------------------- 그 밖의 항목
def _star(cx: float, cy: float, r: float, spikes: int, inner: float) -> list[tuple[float, float]]:
    pts = []
    for i in range(spikes * 2):
        a = -math.pi / 2 + i * math.pi / spikes
        rr = r if i % 2 == 0 else r * inner
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def _badge(img: Image.Image, item: Item, box: Box) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    x0, y0, x1, y1 = box
    c = rgb(item.color, "#e63946")
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = min(x1 - x0, y1 - y0) / 2
    shape = item.shape or "circle"
    if shape == "circle":
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c)
        inner = (round(cx - r * 0.72), round(cy - r * 0.72), round(cx + r * 0.72), round(cy + r * 0.72))
    elif shape == "burst":
        d.polygon(_star(cx, cy, r, 16, 0.84), fill=c)
        inner = (round(cx - r * 0.66), round(cy - r * 0.66), round(cx + r * 0.66), round(cy + r * 0.66))
    else:
        n = (y1 - y0) * 0.22
        d.polygon([(x0, y0), (x1, y0), (x1 - n, (y0 + y1) / 2), (x1, y1), (x0, y1)], fill=c)
        inner = (round(x0 + (x1 - x0) * 0.06), y0, round(x1 - n * 1.3), y1)
    draw_text(img, inner, item.text or "", bold=True, color=(255, 255, 255), pad=2)


def _split(img: Image.Image, item: Item, box: Box) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    x0, y0, x1, y1 = box
    mid, gap = (x0 + x1) // 2, max(3, (x1 - x0) // 80)
    caption_h = round((y1 - y0) * 0.18) if item.text else 0
    top, bottom = y0, y1 - caption_h
    for (a, b), color, label in (((x0, mid - gap), item.left_color or "#9aa0a6", item.left_label), ((mid + gap, x1), item.right_color or "#34c38f", item.right_label)):
        c = rgb(color, "#9aa0a6")
        d.rounded_rectangle((a, top, b, bottom), radius=10, fill=c)
        pw = b - a
        d.ellipse((a + pw * 0.30, top + (bottom - top) * 0.34, a + pw * 0.70, top + (bottom - top) * 0.34 + pw * 0.40), fill=shade(c, 1.35))
        draw_text(img, (a, top + 4, b, top + round((bottom - top) * 0.24)), label or "", bold=True, color=(255, 255, 255), pad=4)
    if item.text:
        draw_text(img, (x0, bottom + 2, x1, y1), item.text, color=rgb(item.color, "#222222"), pad=2)


def _popup(img: Image.Image, item: Item, box: Box) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    x0, y0, x1, y1 = box
    h = y1 - y0
    d.rounded_rectangle((x0 + 4, y0 + 4, x1 + 4, y1 + 4), radius=8, fill=(0, 0, 0, 70))
    d.rounded_rectangle((x0, y0, x1, y1), radius=8, fill=(252, 252, 252), outline=(120, 120, 120), width=2)
    bar = y0 + round(h * 0.16)
    d.rounded_rectangle((x0, y0, x1, bar), radius=8, fill=rgb(item.color, "#c0392b"))
    d.rectangle((x0, y0 + round(h * 0.08), x1, bar), fill=rgb(item.color, "#c0392b"))
    draw_text(img, (x0 + 8, y0 + 2, x1 - 30, bar - 1), item.title or "", bold=True, color=(255, 255, 255), align="left")
    draw_text(img, (x1 - 26, y0 + 2, x1 - 6, bar - 1), "x", bold=True, color=(255, 255, 255))
    btn_h = round(h * 0.2) if item.buttons else 0
    draw_text(img, (x0 + 10, bar + 6, x1 - 10, y1 - btn_h - 10), item.text or "", color=(30, 30, 30), align="left", pad=2)
    if item.buttons:
        n = len(item.buttons)
        bw = (x1 - x0 - 20 - (n - 1) * 8) / n
        for i, label in enumerate(item.buttons):
            bx = x0 + 10 + i * (bw + 8)
            d.rounded_rectangle((bx, y1 - btn_h - 6, bx + bw, y1 - 8), radius=6, fill=(225, 228, 232), outline=(150, 150, 150))
            draw_text(img, (round(bx), y1 - btn_h - 6, round(bx + bw), y1 - 8), label, bold=True, color=(30, 30, 30), pad=3)


def _stars(img: Image.Image, item: Item, box: Box) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    x0, y0, x1, y1 = box
    w = x1 - x0
    span = w * (0.55 if item.text else 1.0)
    r = min(span / 10, (y1 - y0) / 2)
    for i in range(5):
        d.polygon(_star(x0 + r + i * 2 * r, (y0 + y1) / 2, r, 5, 0.42), fill=(247, 181, 0))
    if item.text:
        draw_text(img, (round(x0 + span + 6), y0, x1, y1), item.text, color=rgb(item.color, "#333333"), align="left", pad=2)


def _item(img: Image.Image, item: Item, W: int, H: int) -> None:
    box = _box(item, W, H)
    d = ImageDraw.Draw(img, "RGBA")
    x0, y0, x1, y1 = box
    kind = item.kind
    if kind == "product":
        _product(img, item, box)
    elif kind == "text":
        if item.bg:
            d.rounded_rectangle(box, radius=8, fill=rgb(item.bg, "#ffffff"))
        draw_text(img, box, item.text or "", size=item.size, bold=item.bold, align=item.align, color=rgb(item.color, "#1d2430"),
                  opacity=item.opacity, rotate=item.rotate, pad=4 if item.bg else 0)
    elif kind == "fine_print":
        draw_text(img, box, item.text or "", size=item.size if item.size != "auto" else 11, align=item.align, color=rgb(item.color, "#6b7280"),
                  opacity=item.opacity, valign="top")
    elif kind == "button":
        d.rounded_rectangle(box, radius=min((y1 - y0) // 2, 22), fill=rgb(item.color, "#2b6cff"))
        draw_text(img, box, item.text or "", size=item.size, bold=True, color=(255, 255, 255), opacity=item.opacity, pad=6)
    elif kind == "badge":
        _badge(img, item, box)
    elif kind == "timer":
        d.rounded_rectangle(box, radius=10, fill=(21, 21, 26))
        draw_text(img, box, item.text or "", size=item.size, bold=True, color=rgb(item.color, "#ff4d4f"), opacity=item.opacity, pad=6)
    elif kind == "split":
        _split(img, item, box)
    elif kind == "popup":
        _popup(img, item, box)
    elif kind == "stars":
        _stars(img, item, box)
    elif kind == "logo":
        r = min(x1 - x0, y1 - y0) / 2
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rgb(item.color, "#1d3557"))
        draw_text(img, (round(cx - r * 0.7), round(cy - r * 0.7), round(cx + r * 0.7), round(cy + r * 0.7)), item.text or "", bold=True, color=(255, 255, 255))


def render(spec: ImageSpec) -> Image.Image:
    c = spec.canvas
    img = Image.new("RGBA", (c.w, c.h), (*rgb(c.bg, "#f4f1ea"), 255))
    if c.bg2:
        top, bot = rgb(c.bg, "#f4f1ea"), rgb(c.bg2, "#ffffff")
        d = ImageDraw.Draw(img)
        for y in range(c.h):
            t = y / max(1, c.h - 1)
            d.line([(0, y), (c.w, y)], fill=tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    for item in spec.items:
        _item(img, item, c.w, c.h)
    return img.convert("RGB")


def render_png(spec: ImageSpec, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    render(spec).save(out, format="PNG", optimize=False)
    return out


def problems(spec: ImageSpec) -> list[str]:
    """그릴 수 없는 이유 목록(비어 있으면 그릴 수 있다). 글자 하나하나를 먼저 검사해 한 번에 모두 알려 준다."""
    out = [f"글꼴에 없는 글자 {''.join(bad)!r} — {t[:20]!r}" for t in texts(spec) if (bad := unrenderable(t))]
    if out:
        return out
    try:
        render(spec)
    except RenderError as exc:
        out.append(str(exc))
    return out
