"""카드뉴스 이미지 렌더러.

posts.json 한 편(7장: 표지 + 본문5 + CTA)을 PNG로 그립니다.
텍스트가 길면 자동 줄바꿈 후 폰트를 한 단계씩 줄여 넘침을 방지합니다.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

import fonts
import theme as T


# ── 저수준 헬퍼 ──────────────────────────────────────────
def _w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return int(draw.textbbox((0, 0), text, font=font)[2])


def wrap(draw, text: str, font, max_w: int) -> list[str]:
    """한글은 어절 단위로, 그래도 넘치면 글자 단위로 자른다."""
    if _w(draw, text, font) <= max_w:
        return [text]
    lines, cur = [], ""
    for word in text.split(" "):
        trial = f"{cur} {word}".strip()
        if _w(draw, trial, font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    out = []
    for ln in lines:
        while _w(draw, ln, font) > max_w and len(ln) > 1:
            cut = len(ln)
            while cut > 1 and _w(draw, ln[:cut], font) > max_w:
                cut -= 1
            out.append(ln[:cut])
            ln = ln[cut:]
        out.append(ln)
    return out


def fit_block(draw, lines: list[str], font_fn, size: int, max_w: int,
              max_h: int, lh: float, min_size: int = 22):
    """폰트 크기를 줄여가며 (max_w, max_h) 안에 들어가는 조합을 찾는다."""
    while size >= min_size:
        font = font_fn(size)
        wrapped = []
        for ln in lines:
            wrapped.extend(wrap(draw, ln, font, max_w))
        h = int(len(wrapped) * size * lh)
        if h <= max_h:
            return font, wrapped, h
        size -= 2
    font = font_fn(min_size)
    wrapped = []
    for ln in lines:
        wrapped.extend(wrap(draw, ln, font, max_w))
    return font, wrapped, int(len(wrapped) * min_size * lh)


def draw_block(draw, x: int, y: int, lines: list[str], font, fill, lh: float,
               size: int) -> int:
    step = int(size * lh)
    for i, ln in enumerate(lines):
        draw.text((x, y + i * step), ln, font=font, fill=fill)
    return y + len(lines) * step


def draw_tracked(draw, x: int, y: int, text: str, font, fill, tracking: int = 4):
    """자간을 벌려 그린다(작은 라벨용)."""
    cx = x
    for ch in text:
        draw.text((cx, y), ch, font=font, fill=fill)
        cx += _w(draw, ch, font) + tracking
    return cx


def rule(draw, x1: int, y: int, x2: int, color=T.RULE, weight: int = 2):
    draw.rectangle([x1, y, x2, y + weight - 1], fill=color)


def dots(draw, cx: int, y: int, total: int, active: int):
    gap, r = 22, 5
    start = cx - (total - 1) * gap // 2
    for i in range(total):
        c = T.ACCENT if i == active else T.RULE
        px = start + i * gap
        draw.ellipse([px - r, y - r, px + r, y + r], fill=c)


def footer(draw, book: dict, right_cb=None, left_text: str | None = None,
           left_color=T.MUTED, left_weight: str = "regular"):
    y = T.HEIGHT - T.MARGIN - 34
    rule(draw, T.MARGIN, y - 34, T.WIDTH - T.MARGIN, weight=1)
    f = fonts.sans(T.SZ_FOOT, "regular")
    lf = fonts.sans(T.SZ_FOOT, left_weight)
    draw.text((T.MARGIN, y), left_text or book["brand"]["kicker"], font=lf,
              fill=left_color)
    if right_cb:
        right_cb(draw, y, f)
    return y


# ── 카드 3종 ────────────────────────────────────────────
def card_cover(post: dict, book: dict) -> Image.Image:
    img = Image.new("RGB", (T.WIDTH, T.HEIGHT), T.BG_COVER)
    d = ImageDraw.Draw(img)
    inner = T.WIDTH - T.MARGIN * 2

    # 상단 라벨 + 헤어라인 (책 표지의 부제/괘선 구조를 그대로 가져옴)
    draw_tracked(d, T.MARGIN, T.MARGIN, book["brand"]["kicker"],
                 fonts.sans(T.SZ_KICKER, "medium"), T.MUTED, tracking=5)
    rule(d, T.MARGIN, T.MARGIN + 62, T.WIDTH - T.MARGIN, weight=1)

    # 후킹 문구
    font, lines, h = fit_block(d, post["cover"], fonts.serif, T.SZ_COVER_HOOK,
                               inner, 620, T.LH_HOOK, min_size=44)
    top = (T.HEIGHT - h) // 2 - 60
    rule(d, T.MARGIN, top - 56, T.MARGIN + 110, color=T.ACCENT, weight=5)
    end = draw_block(d, T.MARGIN, top, lines, font, T.INK, T.LH_HOOK, font.size)

    # 부제
    sub_font, sub_lines, _ = fit_block(d, [post.get("cover_sub", "")], fonts.sans,
                                       T.SZ_COVER_SUB, inner, 200, 1.5, min_size=24)
    draw_block(d, T.MARGIN, end + 44, sub_lines, sub_font, T.MUTED, 1.5, sub_font.size)

    def right(draw, y, f):
        t = book["brand"]["author_line"]
        draw.text((T.WIDTH - T.MARGIN - _w(draw, t, f), y), t, font=f, fill=T.MUTED)

    footer(d, book, right, left_text=f"WEEK {post['week']:02d}",
           left_color=T.ACCENT, left_weight="medium")
    return img


def card_body(post: dict, book: dict, idx: int) -> Image.Image:
    slide = post["slides"][idx]
    img = Image.new("RGB", (T.WIDTH, T.HEIGHT), T.BG)
    d = ImageDraw.Draw(img)
    inner = T.WIDTH - T.MARGIN * 2

    # 본문이 놓일 수 있는 세로 영역 (헤더 여백 ~ 푸터 위)
    area_top = T.MARGIN + 20
    area_bot = T.HEIGHT - T.MARGIN - 110
    area_h = area_bot - area_top

    NUM_H, GAP_RULE, GAP_BODY = 96, 26, 56

    # 1) 먼저 측정
    tf, tlines, th = fit_block(d, [slide["title"]], fonts.serif, T.SZ_TITLE,
                               inner, 220, T.LH_TITLE, min_size=38)
    body_budget = area_h - NUM_H - th - GAP_RULE - GAP_BODY
    bf, blines, bh = fit_block(d, slide["body"],
                               lambda s: fonts.sans(s, "regular"),
                               T.SZ_BODY, inner, body_budget, T.LH_BODY,
                               min_size=26)

    # 2) 전체 블록을 세로 가운데로
    total = NUM_H + th + GAP_RULE + GAP_BODY + bh
    y = area_top + max(0, (area_h - total) // 2)

    nf = fonts.serif(T.SZ_NUM)
    num = f"{idx + 1:02d}"
    d.text((T.MARGIN, y), num, font=nf, fill=T.ACCENT)
    nx = T.MARGIN + _w(d, num, nf) + 20
    rule(d, nx, y + T.SZ_NUM // 2 + 4, nx + 90, color=T.SAND, weight=3)
    y += NUM_H

    y = draw_block(d, T.MARGIN, y, tlines, tf, T.INK, T.LH_TITLE, tf.size)
    y += GAP_RULE
    rule(d, T.MARGIN, y, T.MARGIN + 72, color=T.RULE, weight=2)
    y += GAP_BODY

    draw_block(d, T.MARGIN, y, blines, bf, T.BODY, T.LH_BODY, bf.size)

    footer(d, book, lambda dr, yy, f: dots(dr, T.WIDTH - T.MARGIN - 70,
                                           yy + f.size // 2, 5, idx))
    return img


def card_cta(post: dict, book: dict, variant: str = "default") -> Image.Image:
    # 채널마다 마지막 장의 안내 문구가 달라야 합니다.
    #   인스타 → "프로필 링크에서 만나보세요"
    #   링크드인 → "첫 번째 댓글에 링크를 남겼습니다"
    cta = book.get(f"cta_{variant}", book["cta"]) if variant != "default" else book["cta"]
    img = Image.new("RGB", (T.WIDTH, T.HEIGHT), T.BG_COVER)
    d = ImageDraw.Draw(img)
    inner = T.WIDTH - T.MARGIN * 2

    draw_tracked(d, T.MARGIN, T.MARGIN, book["brand"]["kicker"],
                 fonts.sans(T.SZ_KICKER, "medium"), T.MUTED, tracking=5)
    rule(d, T.MARGIN, T.MARGIN + 62, T.WIDTH - T.MARGIN, weight=1)

    hf, hlines, hh = fit_block(d, cta["headline"], fonts.serif, T.SZ_CTA_HEAD,
                               inner, 300, 1.4, min_size=42)
    bf, blines, bh = fit_block(d, cta["body"], lambda s: fonts.sans(s, "regular"),
                               T.SZ_CTA_BODY, inner, 300, 1.65, min_size=26)
    total = hh + 40 + bh + 70 + 108 + 34 + 40
    area_top, area_bot = T.MARGIN + 130, T.HEIGHT - T.MARGIN - 110
    y = area_top + max(0, (area_bot - area_top - total) // 2)

    rule(d, T.MARGIN, y - 56, T.MARGIN + 110, color=T.ACCENT, weight=5)
    y = draw_block(d, T.MARGIN, y, hlines, hf, T.INK, 1.4, hf.size) + 40
    y = draw_block(d, T.MARGIN, y, blines, bf, T.BODY, 1.65, bf.size) + 70

    # 액션 박스
    af = fonts.sans(T.SZ_CTA_ACTION, "bold")
    box_h = 108
    d.rounded_rectangle([T.MARGIN, y, T.WIDTH - T.MARGIN, y + box_h],
                        radius=10, outline=T.ACCENT, width=3)
    tw = _w(d, cta["action"], af)
    d.text(((T.WIDTH - tw) // 2, y + (box_h - T.SZ_CTA_ACTION) // 2 - 6),
           cta["action"], font=af, fill=T.ACCENT)
    y += box_h + 34

    ff = fonts.sans(28, "regular")
    fw = _w(d, cta["footer"], ff)
    d.text(((T.WIDTH - fw) // 2, y), cta["footer"], font=ff, fill=T.MUTED)

    def right(draw, yy, f):
        t = book["brand"]["author_line"]
        draw.text((T.WIDTH - T.MARGIN - _w(draw, t, f), yy), t, font=f, fill=T.MUTED)

    footer(d, book, right)
    return img


# ── 진입점 ──────────────────────────────────────────────
def render_post(post: dict, book: dict, outdir: str,
                cta_variant: str = "default") -> list[str]:
    """한 편(7장)을 PNG로 저장하고 경로 리스트를 순서대로 반환.

    cta_variant 로 마지막 CTA 장의 안내 문구를 채널에 맞게 바꿉니다.
    """
    os.makedirs(outdir, exist_ok=True)
    paths = []
    cards = [("01_cover", card_cover(post, book))]
    for i in range(len(post["slides"])):
        cards.append((f"{i + 2:02d}_body{i + 1}", card_body(post, book, i)))
    cards.append((f"{len(post['slides']) + 2:02d}_cta",
                  card_cta(post, book, cta_variant)))

    for name, im in cards:
        p = os.path.join(outdir, f"w{post['week']:02d}_{name}.png")
        im.save(p, "PNG", optimize=True)
        paths.append(p)
    return paths
