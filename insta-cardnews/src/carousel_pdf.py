"""카드 7장 → 링크드인 문서(PDF) 캐러셀.

링크드인은 PDF를 올리면 좌우로 넘겨보는 캐러셀로 렌더링합니다.
단일 이미지보다 체류 시간이 길어 도달에 유리한 형식입니다.

카드 원본 비율(1080×1350, 4:5)을 그대로 페이지 크기로 써서
여백이나 잘림 없이 화면을 가득 채우게 만듭니다.

PDF 생성에 Pillow만 사용합니다. 카드 렌더링에 이미 쓰고 있는 라이브러리라
추가로 설치할 것이 없습니다.
"""

from __future__ import annotations

import logging
import os

from PIL import Image

log = logging.getLogger(__name__)

# 72dpi 로 저장하면 1픽셀 = 1포인트가 되어 카드 크기가 그대로 페이지 크기가 됩니다.
DPI = 72.0


def build(card_paths: list[str], out_path: str, title: str = "",
          author: str = "") -> str:
    if not card_paths:
        raise ValueError("카드 이미지가 없습니다.")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    pages = [Image.open(p).convert("RGB") for p in card_paths]
    try:
        pages[0].save(
            out_path,
            "PDF",
            save_all=True,
            append_images=pages[1:],
            resolution=DPI,
            title=title or None,
            author=author or None,
        )
    finally:
        for im in pages:
            im.close()

    size_kb = os.path.getsize(out_path) / 1024
    log.info("PDF 캐러셀 생성: %s (%d쪽, %.0fKB)", out_path, len(card_paths), size_kb)
    return out_path
