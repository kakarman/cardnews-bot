"""한글 폰트 해석기.

Noto CJK는 여러 언어 얼굴이 하나의 .ttc 안에 들어 있어서,
한국어(KR) 얼굴의 index를 이름으로 찾아 씁니다.
로컬/GitHub Actions 어디서 돌려도 동작하도록 후보 경로를 순회합니다.
"""

from __future__ import annotations

import os
from functools import lru_cache

from PIL import ImageFont

# 우선순위대로 탐색할 후보 경로
SERIF_CANDIDATES = [
    "assets/fonts/NotoSerifKR-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-SemiBold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSerifCJK-Bold.ttc",
]
SANS_CANDIDATES = {
    "regular": [
        "assets/fonts/NotoSansKR-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ],
    "medium": [
        "assets/fonts/NotoSansKR-Medium.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ],
    "bold": [
        "assets/fonts/NotoSansKR-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    ],
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _abs(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def _korean_index(path: str) -> int:
    """.ttc 안에서 'CJK KR' 얼굴의 index를 찾는다. 단일 ttf면 0."""
    if not path.lower().endswith(".ttc"):
        return 0
    for i in range(12):
        try:
            name = ImageFont.truetype(path, 12, index=i).getname()[0]
        except Exception:
            break
        if "KR" in name and "Mono" not in name:
            return i
    return 0


def _resolve(candidates: list[str]) -> tuple[str, int]:
    for c in candidates:
        p = _abs(c)
        if os.path.exists(p):
            return p, _korean_index(p)
    raise FileNotFoundError(
        "한글 폰트를 찾지 못했습니다.\n"
        "  Ubuntu/GitHub Actions:  sudo apt-get install -y fonts-noto-cjk\n"
        "  macOS:                  brew install --cask font-noto-serif-cjk-kr\n"
        f"  탐색한 경로: {candidates}"
    )


@lru_cache(maxsize=64)
def serif(size: int) -> ImageFont.FreeTypeFont:
    path, idx = _resolve(SERIF_CANDIDATES)
    return ImageFont.truetype(path, size, index=idx)


@lru_cache(maxsize=64)
def sans(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    path, idx = _resolve(SANS_CANDIDATES[weight])
    return ImageFont.truetype(path, size, index=idx)


def selftest() -> str:
    s, sa = serif(40), sans(40)
    return f"serif={s.getname()}  sans={sa.getname()}"


if __name__ == "__main__":
    print(selftest())
