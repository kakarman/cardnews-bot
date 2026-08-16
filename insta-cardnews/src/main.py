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


def pick_post(posts: list, state: dict, week: int | None, key: str = "next_index"):
    if week is not None:
        for p in posts:
            if p["week"] == week:
                return p
        raise SystemExit(f"week {week} 회차를 찾을 수 없습니다.")
    idx = state.get(key, 0)
    if idx >= len(posts):
        if not state.get("loop", True):
            raise SystemExit(f"모든 회차를 발행했습니다. state.json 의 {key} 를 0으로 되돌리거나 새 글을 추가하세요.")
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


def build_threads_chain(post: dict, book: dict, cover_url: str | None) -> list[dict]:
    """한 회차를 스레드 3연속 포스트로 구성한다.

      1) 루트  : 표지 카드 이미지 + 후킹 문구 + 요약
      2) 답글1 : 본문 슬라이드 핵심 (텍스트)
      3) 답글2 : 구매 링크  ← 스레드는 링크가 실제로 클릭됩니다
    """
    from threads import clamp

    cfg = book["threads"]
    root = cfg["root_template"].format(hook=post["hook"], summary=post["summary"])

    # 슬라이드를 문장으로 되살려 담되, 500자 예산 안에서만 채운다
    lines, budget = [cfg["body_intro"]], 420
    used = len(lines[0])
    for slide in post["slides"][: cfg.get("max_body_slides", 3)]:
        block = f"\n▸ {slide['title']}\n" + " ".join(slide["body"])
        if used + len(block) > budget:
            break
        lines.append(block)
        used += len(block)
    body = "\n".join(lines)

    cta = cfg["cta_template"].format(buy_url=book["buy_url"])

    chain = [{"text": clamp(root)}]
    if cover_url:
        chain[0]["image_url"] = cover_url
    chain.append({"text": clamp(body)})
    chain.append({"text": clamp(cta)})
    return chain


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


TOKEN_LIFETIME_DAYS = 60


def _emit(key: str, value) -> None:
    """GitHub Actions 스텝 출력으로 값을 넘긴다 (로컬에서는 무시)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def cmd_check(args):
    c = _client()
    me = c.whoami()
    log.info("인증 방식 : %s (%s)", c.auth, c.host)
    log.info("연결 OK   : @%s (id=%s)", me.get("username"), me.get("id"))
    base = os.environ.get("PUBLIC_BASE_URL", "")
    log.info("이미지 URL: %s", base or "(미설정 — 게시 시 실패합니다)")

    # 토큰 남은 기간 계산 (state.json 의 token_issued_at 기준)
    state = load_state()
    issued = state.get("token_issued_at", "")
    days_left = None
    if issued:
        try:
            d0 = datetime.fromisoformat(issued).date()
            used = (datetime.now(timezone.utc).date() - d0).days
            days_left = TOKEN_LIFETIME_DAYS - used
            log.info("토큰 발급 : %s (%d일 경과, 약 %d일 남음)", issued, used, days_left)
        except ValueError:
            log.warning("token_issued_at 형식이 올바르지 않습니다: %s (예: 2026-08-16)", issued)
    else:
        log.info("토큰 발급 : (state.json 의 token_issued_at 미설정 — 만료 예고 없이 동작합니다)")

    warn = args.warn_days is not None and days_left is not None and days_left <= args.warn_days
    _emit("days_left", days_left if days_left is not None else "")
    _emit("needs_refresh", "true" if warn else "false")
    if warn:
        log.warning("⚠️ 토큰 만료가 %d일 남았습니다. 갱신이 필요합니다.", days_left)


def cmd_refresh(args):
    data = _client().refresh_token()
    days = int(data.get("expires_in", 0)) // 86400
    today = datetime.now(timezone.utc).date().isoformat()
    log.info("토큰을 %d일 더 연장했습니다.", days)
    log.info("\n① 아래 값을 GitHub → Settings → Secrets → IG_ACCESS_TOKEN 에 붙여넣으세요:\n")
    log.info(data["access_token"])
    log.info("\n② state.json 의 token_issued_at 을 오늘 날짜로 바꿔주세요:")
    log.info('   "token_issued_at": "%s"', today)


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


def cmd_linkedin(args):
    """링크드인 수동 게시용 원고 24편을 마크다운 한 파일로 만든다."""
    book, posts = load()
    with open(os.path.join(CONTENT, "linkedin.json"), encoding="utf-8") as f:
        cfg = json.load(f)

    out = [
        "# 링크드인 게시용 원고 24편",
        "",
        "주 1회, 화요일이나 수요일 **오전 8~10시**에 올리는 것을 권합니다.",
        "",
        "**게시 방법**",
        "",
        "1. 아래 「본문」을 그대로 복사해 링크드인 글쓰기에 붙여넣기",
        "2. 게시 후 **바로 「첫 댓글」을 직접 댓글로 등록**",
        "   (본문에 외부 링크를 넣으면 링크드인이 도달을 줄입니다)",
        "3. 댓글이 달리면 되도록 답글을 남기세요. 추가 노출로 이어집니다.",
        "",
        "---",
        "",
        f"## 첫 댓글 (24편 공통)",
        "",
        "```",
        cfg["first_comment"].format(buy_url=book["buy_url"]),
        "```",
        "",
        "---",
        "",
    ]

    for post in posts:
        q = cfg["questions"].get(str(post["week"]), "")
        body = [post["hook"], "", post["summary"], ""]
        for slide in post["slides"][: cfg.get("max_body_slides", 3)]:
            body.append(f"▸ {slide['title']}")
            body.append(" ".join(slide["body"]))
            body.append("")
        if q:
            body += [q, ""]
        body.append(cfg["cta"])
        body.append("")
        body.append(" ".join(cfg["hashtags"] + post.get("hashtags", [])[:2]))

        text = "\n".join(body)
        out += [
            f"## {post['week']}회차 — {post['chapter']}",
            "",
            f"*{len(text)}자 · 첫 줄이 후킹입니다*",
            "",
            "```",
            text,
            "```",
            "",
            "---",
            "",
        ]

    path = os.path.join(ROOT, "docs", "LINKEDIN_POSTS.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    log.info("링크드인 원고 %d편 생성 → %s", len(posts), os.path.relpath(path, ROOT))


def cmd_video(args):
    """릴스·쇼츠용 세로 영상(mp4) 생성."""
    import video

    book, posts = load()
    state = load_state()
    targets = posts if args.all else [pick_post(posts, state, args.week)]

    outdir = os.path.join(ROOT, "output", "video")
    made = []
    for post in targets:
        cards = render.render_post(
            post, book, os.path.join(CARDS_DIR, f"w{post['week']:02d}"))
        out = os.path.join(outdir, f"w{post['week']:02d}_reels.mp4")
        video.build(cards, out)
        made.append(out)
        log.info("  WEEK %02d · %s", post["week"], post["chapter"])

    log.info("\n%d개 영상을 %s 에 저장했습니다.", len(made), outdir)
    log.info("업로드할 때 앱에서 유행하는 음악을 직접 얹으면 도달이 훨씬 좋아집니다.")


def cmd_threads(args):
    book, posts = load()
    state = load_state()
    post = pick_post(posts, state, args.week, key="threads_next_index")

    # 표지 카드를 루트 포스트 이미지로 쓰기 위해 렌더링
    outdir = os.path.join(CARDS_DIR, f"w{post['week']:02d}")
    paths = render.render_post(post, book, outdir)
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    cover_url = (f"{base}/w{post['week']:02d}/{os.path.basename(paths[0])}"
                 if base else None)

    chain = build_threads_chain(post, book, cover_url)

    log.info("═" * 56)
    log.info("[스레드] WEEK %02d · %s", post["week"], post["chapter"])
    log.info("═" * 56)
    for i, part in enumerate(chain, 1):
        tag = "루트" if i == 1 else f"답글 {i - 1}"
        log.info("\n─ %s (%d자) ─", tag, len(part["text"]))
        if part.get("image_url"):
            log.info("[이미지] %s", part["image_url"])
        log.info("%s", part["text"])

    if args.dry_run:
        log.info("\n[dry-run] 실제 게시는 하지 않았습니다.")
        return

    from threads import ThreadsClient
    client = ThreadsClient(
        access_token=os.environ.get("THREADS_ACCESS_TOKEN", ""),
        user_id=os.environ.get("THREADS_USER_ID", ""),
    )
    log.info("")
    result = client.post_chain(chain)
    log.info("게시 완료 → %s", result.get("permalink") or result["root_id"])

    if args.week is None:
        state["threads_next_index"] = (state.get("threads_next_index", 0) + 1) % len(posts)
    state.setdefault("threads_history", []).append({
        "week": post["week"],
        "chapter": post["chapter"],
        "root_id": result["root_id"],
        "permalink": result.get("permalink", ""),
        "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    save_state(state)
    log.info("다음 스레드 회차 인덱스: %d", state.get("threads_next_index", 0))


def main():
    ap = argparse.ArgumentParser(description="IT기업에서 하루하루 어휴 — 주간 카드뉴스 봇")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="카드 이미지만 생성")
    r.add_argument("--week", type=int)
    r.add_argument("--all", action="store_true")
    r.set_defaults(func=cmd_render)

    c = sub.add_parser("check", help="토큰·계정 연결 점검")
    c.add_argument("--warn-days", type=int,
                   help="토큰 만료가 N일 이내면 경고 신호를 낸다")
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("refresh", help="장기 토큰을 60일 더 연장")
    f.set_defaults(func=cmd_refresh)

    p = sub.add_parser("post", help="인스타그램에 게시")
    p.add_argument("--week", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_post)

    li = sub.add_parser("linkedin", help="링크드인 게시용 원고 24편 생성")
    li.set_defaults(func=cmd_linkedin)

    v = sub.add_parser("video", help="릴스·쇼츠용 세로 영상(mp4) 생성")
    v.add_argument("--week", type=int)
    v.add_argument("--all", action="store_true")
    v.set_defaults(func=cmd_video)

    t = sub.add_parser("threads", help="스레드에 3연속 포스트로 게시")
    t.add_argument("--week", type=int)
    t.add_argument("--dry-run", action="store_true")
    t.set_defaults(func=cmd_threads)

    args = ap.parse_args()
    try:
        args.func(args)
    except Exception as e:  # 스택 트레이스 대신 읽을 수 있는 메시지로
        from instagram import InstagramError
        from threads import ThreadsError
        if isinstance(e, (InstagramError, ThreadsError)):
            doc = "docs/SETUP_THREADS.md" if isinstance(e, ThreadsError) else "docs/SETUP.md"
            log.error("\n❌ %s\n\n해결 방법은 %s 의 '문제 해결' 표를 확인하세요.", e, doc)
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
