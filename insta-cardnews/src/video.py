"""카드 7장 → 릴스/쇼츠용 세로 영상(mp4) 변환.

인스타 릴스와 유튜브 쇼츠는 9:16(1080×1920)을 씁니다.
카드는 4:5(1080×1350)라, 위아래를 책 톤의 여백으로 채워 세로로 맞춥니다.

소리는 기본적으로 '무음 오디오 트랙'을 넣습니다.
인스타 릴스는 오디오 트랙이 아예 없는 영상을 거부하는 경우가 있어서입니다.

배경음악을 넣고 싶으면 assets/audio/ 폴더에 bgm.mp3 (또는 .m4a/.wav) 를 두세요.
자동으로 영상 길이에 맞춰 깔리고 끝에서 2초간 페이드아웃됩니다.
⚠️ 저작권이 해결된 음원(무료 BGM, 직접 만든 곡 등)만 쓰세요.
   유명 가요를 넣으면 게시물이 내려가거나 음소거될 수 있습니다.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw

import fonts
import theme as T

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BGM_CANDIDATES = ["bgm.mp3", "bgm.m4a", "bgm.wav"]
BGM_VOLUME = 0.6

W, H = 1080, 1920          # 9:16 세로
FPS = 30
COVER_SEC = 3.0            # 표지는 조금 길게 (스크롤을 멈추게 하는 구간)
BODY_SEC = 3.0
CTA_SEC = 4.0              # 마지막 CTA는 읽을 시간을 더 줌


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _to_vertical(card_path: str, index: int, total: int) -> Image.Image:
    """4:5 카드를 9:16 캔버스 가운데에 얹고 상·하단을 채운다."""
    canvas = Image.new("RGB", (W, H), T.BG)
    card = Image.open(card_path).convert("RGB")

    scale = W / card.width
    card = card.resize((W, int(card.height * scale)), Image.LANCZOS)
    top = (H - card.height) // 2
    canvas.paste(card, (0, top))

    d = ImageDraw.Draw(canvas)

    # 상단: 진행 막대 (몇 장 중 몇 번째인지 한눈에)
    bar_y, bar_w = 96, int(W * 0.62)
    x0 = (W - bar_w) // 2
    d.rounded_rectangle([x0, bar_y, x0 + bar_w, bar_y + 8], radius=4, fill=T.RULE)
    filled = int(bar_w * (index + 1) / total)
    d.rounded_rectangle([x0, bar_y, x0 + filled, bar_y + 8], radius=4, fill=T.ACCENT)

    # 하단: 책 제목
    f = fonts.sans(30, "medium")
    label = "IT기업에서 하루하루 어휴"
    tw = int(d.textbbox((0, 0), label, font=f)[2])
    d.text(((W - tw) // 2, H - 150), label, font=f, fill=T.MUTED)

    return canvas


def find_bgm() -> str | None:
    folder = os.path.join(ROOT, "assets", "audio")
    for name in BGM_CANDIDATES:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return p
    return None


def build(card_paths: list[str], out_path: str,
          durations: list[float] | None = None) -> str:
    """카드 이미지들을 이어 붙여 mp4로 만든다."""
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg 가 설치되어 있지 않습니다.\n"
            "  Ubuntu : sudo apt-get install -y ffmpeg\n"
            "  macOS  : brew install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html"
        )

    n = len(card_paths)
    if durations is None:
        durations = [COVER_SEC] + [BODY_SEC] * (n - 2) + [CTA_SEC]

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for i, p in enumerate(card_paths):
            fp = os.path.join(tmp, f"f{i:02d}.png")
            _to_vertical(p, i, n).save(fp, "PNG")
            frames.append(fp)

        # concat demuxer 용 목록 (마지막 프레임은 duration 이 무시되므로 한 번 더 적어줌)
        listfile = os.path.join(tmp, "list.txt")
        with open(listfile, "w", encoding="utf-8") as f:
            for fp, sec in zip(frames, durations):
                f.write(f"file '{fp}'\nduration {sec}\n")
            f.write(f"file '{frames[-1]}'\n")

        total = sum(durations)
        bgm = find_bgm()
        if bgm:
            # 음악을 반복해 영상 길이를 채우고, 끝 2초 페이드아웃
            audio_in = ["-stream_loop", "-1", "-i", bgm]
            fade_at = max(0.0, total - 2.0)
            audio_filter = ["-af", f"volume={BGM_VOLUME},afade=t=out:st={fade_at}:d=2"]
            log.info("배경음악 사용: %s", os.path.relpath(bgm, ROOT))
        else:
            # 무음 트랙 (릴스 호환용)
            audio_in = ["-f", "lavfi", "-i",
                        "anullsrc=channel_layout=stereo:sample_rate=44100"]
            audio_filter = []

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", listfile,
            *audio_in,
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", f"fps={FPS},format=yuv420p",
            *audio_filter,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-t", f"{total:.2f}",
            "-movflags", "+faststart",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg 실패:\n{r.stderr[:600]}")

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    log.info("영상 생성 완료: %s (%.0f초, %.1fMB, %dx%d)",
             out_path, total, size_mb, W, H)
    return out_path
