"""카드 7장 → 링크드인 문서(PDF) 캐러셀.

링크드인은 PDF를 올리면 좌우로 넘겨보는 캐러셀로 렌더링합니다.
단일 이미지보다 체류 시간이 길어 도달에 유리한 형식입니다.

카드 원본 비율(1080×1350, 4:5)을 그대로 페이지 크기로 써서
여백이나 잘림 없이 화면을 가득 채우게 만듭니다.
"""

from __future__ import annotations

import logging
import os

from PIL import Image
from reportlab.pdfgen import canvas

log = logging.getLogger(__name__)


def build(card_paths: list[str], out_path: str, title: str = "",
          author: str = "") -> str:
    if not card_paths:
        raise ValueError("카드 이미지가 없습니다.")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    with Image.open(card_paths[0]) as im:
        page_w, page_h = im.size

    c = canvas.Canvas(out_path, pagesize=(page_w, page_h))
    if title:
        c.setTitle(title)
    if author:
        c.setAuthor(author)

    for p in card_paths:
        # 카드 비율이 다르면 페이지도 그에 맞춰 바꿔 잘림을 막는다
        with Image.open(p) as im:
            w, h = im.size
        c.setPageSize((w, h))
        c.drawImage(p, 0, 0, width=w, height=h)
        c.showPage()

    c.save()
    size_kb = os.path.getsize(out_path) / 1024
    log.info("PDF 캐러셀 생성: %s (%d쪽, %.0fKB)", out_path, len(card_paths), size_kb)
    return out_path
