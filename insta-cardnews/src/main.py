#!/usr/bin/env python3
"""주간 카드뉴스 파이프라인.

  python src/main.py render            # 이번 회차 카드만 그려서 output/ 에 저장
  python src/main.py render --week 5   # 특정 회차만
  python src/main.py render --all      # 24주치 전부 미리보기
  python src/main.py check             # 토큰·계정 연결만 점검 (게시 안 함)
  python src/main.py post --dry-run    # 캡션/댓글/이미지 URL만 출력
  python src/main.py post              # 실제 게시 + 첫 댓글 + 회차 넘기기

환경변수 (.env 또는 GitHub Secrets):
  IG_ACCESS_TOKEN     장기 액세스 토큰                       [필수]
  PUBLIC_BASE_URL     카드 이미지가 공개된 베이스 URL         [필수]
                      예) https://raw.githubusercontent.com/user/repo/main/docs/cards
  IG_AUTH_MODE        instagram(기본) | facebook            [선택]
  IG_USER_ID          계정 ID. instagram 모드에서는 비워두면 자동 조회 [선택]
  GRAPH_API_VERSION   기본 v23.0                            [선택]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import render  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(ROOT, "content")
STATE_PATH = os.path.join(ROOT, "state.json")
CARDS_DIR = os.path.join(ROOT, "docs", "cards")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("cardnews")


# ── 데이터 ──────────────────────────────────────────────
def load():
    with open(os.path.join(CONTENT, "book.json"), encoding="utf-8") as f:
        book = json.load(f)
    with open(os.path.join(CONTENT, "posts.json"), encoding="utf-8") as f:
        posts = json.load(f)
    return book, posts


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"next_index": 0, "loop": True, "history": []}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def pick_post(posts: list, state: dict, week: int | None):
    if week is not None:
        for p in posts:
            if p["week"] == week:
                return p
        raise SystemExit(f"week {week} 회차를 찾을 수 없습니다.")
    idx = state.get("next_index", 0)
    if idx >= len(posts):
        if not state.get("loop", True):
            raise SystemExit("모든 회차를 발행했습니다. state.json 의 next_index 를 0으로 되돌리거나 새 글을 추가하세요.")
        idx = 0
    return posts[idx]


# ── 캡션 ────────────────────────────────────────────────
def build_caption(post: dict, book: dict) -> str:
    tags = book["base_hashtags"] + post.get("hashtags", [])
    seen, uniq = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    uniq = uniq[:30]  # 인스타 해시태그 상한
    return book["caption_template"].format(
        hook=post["hook"],
        summary=post["summary"],
        hashtags=" ".join(uniq),
    )


def build_comment(book: dict) -> str:
    return book["comment_template"].format(buy_url=book["buy_url"])


# ── 명령 ────────────────────────────────────────────────
def cmd_render(args):
    book, posts = load()
    if args.all:
        outdir = os.path.join(ROOT, "output", "preview")
        for p in posts:
            render.render_post(p, book, outdir)
        log.info("24주치 미리보기를 %s 에 저장했습니다.", outdir)
        return

    state = load_state()
    post = pick_post(posts, state, args.week)
    outdir = os.path.join(CARDS_DIR, f"w{post['week']:02d}")
    paths = render.render_post(post, book, outdir)
    log.info("WEEK %02d · %s", post["week"], post["chapter"])
    for p in paths:
        log.info("  %s", os.path.relpath(p, ROOT))
    log.info("\n─ 캡션 미리보기 ─\n%s", build_caption(post, book))


def _client():
    from instagram import InstagramClient
    return InstagramClient(
        user_id=os.environ.get("IG_USER_ID", ""),
        access_token=os.environ.get("IG_ACCESS_TOKEN", ""),
        version=os.environ.get("GRAPH_API_VERSION", "v23.0"),
        auth=os.environ.get("IG_AUTH_MODE", "instagram"),
    )


def cmd_check(args):
    c = _client()
    me = c.whoami()
    log.info("인증 방식 : %s (%s)", c.auth, c.host)
    log.info("연결 OK   : @%s (id=%s)", me.get("username"), me.get("id"))
    base = os.environ.get("PUBLIC_BASE_URL", "")
    log.info("이미지 URL: %s", base or "(미설정 — 게시 시 실패합니다)")


def cmd_refresh(args):
    data = _client().refresh_token()
    days = int(data.get("expires_in", 0)) // 86400
    log.info("토큰을 %d일 더 연장했습니다.", days)
    log.info("\n아래 값을 GitHub → Settings → Secrets → IG_ACCESS_TOKEN 에 붙여넣으세요:\n")
    log.info(data["access_token"])


def cmd_post(args):
    book, posts = load()
    state = load_state()
    post = pick_post(posts, state, args.week)

    outdir = os.path.join(CARDS_DIR, f"w{post['week']:02d}")
    paths = render.render_post(post, book, outdir)

    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    urls = [f"{base}/w{post['week']:02d}/{os.path.basename(p)}" for p in paths]
    caption = build_caption(post, book)
    comment = build_comment(book)

    log.info("═" * 56)
    log.info("WEEK %02d · %s", post["week"], post["chapter"])
    log.info("═" * 56)
    for u in urls:
        log.info("  %s", u)
    log.info("\n─ 캡션 ─\n%s\n", caption)
    log.info("─ 첫 댓글 ─\n%s\n", comment)

    if args.dry_run:
        log.info("[dry-run] 실제 게시는 하지 않았습니다.")
        return

    if not base:
        raise SystemExit("PUBLIC_BASE_URL 이 설정되지 않아 게시할 수 없습니다.")

    result = _client().post_carousel(urls, caption, first_comment=comment)
    log.info("게시 완료 → %s", result.get("permalink") or result["media_id"])

    # --week 로 특정 회차를 수동 발행한 경우엔 주간 순서를 건드리지 않는다
    if args.week is None:
        state["next_index"] = (state.get("next_index", 0) + 1) % len(posts)
    state.setdefault("history", []).append({
        "week": post["week"],
        "chapter": post["chapter"],
        "media_id": result["media_id"],
        "permalink": result.get("permalink", ""),
        "comment_id": result.get("comment_id", ""),
        "comment_error": result.get("comment_error", ""),
        "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    save_state(state)
    log.info("다음 회차 인덱스: %d", state["next_index"])


def main():
    ap = argparse.ArgumentParser(description="IT기업에서 하루하루 어휴 — 주간 카드뉴스 봇")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="카드 이미지만 생성")
    r.add_argument("--week", type=int)
    r.add_argument("--all", action="store_true")
    r.set_defaults(func=cmd_render)

    c = sub.add_parser("check", help="토큰·계정 연결 점검")
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("refresh", help="장기 토큰을 60일 더 연장")
    f.set_defaults(func=cmd_refresh)

    p = sub.add_parser("post", help="인스타그램에 게시")
    p.add_argument("--week", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_post)

    args = ap.parse_args()
    try:
        args.func(args)
    except Exception as e:  # 스택 트레이스 대신 읽을 수 있는 메시지로
        from instagram import InstagramError
        if isinstance(e, InstagramError):
            log.error("\n❌ %s\n\n해결 방법은 docs/SETUP.md 의 '문제 해결' 표를 확인하세요.", e)
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
