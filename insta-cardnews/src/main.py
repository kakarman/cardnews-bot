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
import importlib
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# render 는 Pillow(이미지 라이브러리)를 끌어옵니다.
# check / refresh 처럼 이미지가 필요 없는 명령까지 Pillow 를 요구하면
# 가벼운 환경에서 엉뚱한 ModuleNotFoundError 로 실패합니다.
# 그래서 실제로 쓰는 함수 안에서만 불러옵니다.
def _render():
    import render
    return render

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


DEFAULT_STATE = {"next_index": 0, "loop": True, "token_issued_at": "", "history": []}


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return dict(DEFAULT_STATE)

    with open(STATE_PATH, encoding="utf-8") as f:
        raw = f.read()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # 손으로 고치다 쉼표를 빠뜨리는 경우가 가장 흔합니다.
        # 어느 줄이 문제인지 그대로 보여줘야 바로 고칠 수 있습니다.
        lines = raw.splitlines()
        lo, hi = max(0, e.lineno - 3), min(len(lines), e.lineno + 2)
        snippet = "\n".join(
            f"  {'▶' if i + 1 == e.lineno else ' '} {i + 1:2d} | {lines[i]}"
            for i in range(lo, hi)
        )
        raise SystemExit(
            f"\n❌ state.json 을 읽을 수 없습니다 (JSON 문법 오류)\n"
            f"   {e.msg} — {e.lineno}번째 줄 {e.colno}번째 칸\n\n"
            f"{snippet}\n\n"
            f"   가장 흔한 원인은 ▶ 표시된 줄의 '바로 윗줄' 끝에 쉼표(,)가 빠진 것입니다.\n"
            f"   JSON은 마지막 항목을 뺀 모든 줄 끝에 쉼표가 있어야 합니다.\n\n"
            f"   올바른 예:\n"
            f'     {{\n'
            f'       "next_index": 1,\n'
            f'       "loop": true,\n'
            f'       "token_issued_at": "2026-08-16",   ← 쉼표 필요\n'
            f'       "history": []\n'
            f'     }}\n'
        )


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
            _render().render_post(p, book, outdir)
        log.info("24주치 미리보기를 %s 에 저장했습니다.", outdir)
        return

    state = load_state()
    post = pick_post(posts, state, args.week)
    outdir = os.path.join(CARDS_DIR, f"w{post['week']:02d}")
    paths = _render().render_post(post, book, outdir)
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
    # 토큰 확인을 가장 먼저 합니다.
    # 이 단계를 통과하면 token_ok=true 를 남기므로,
    # 이후 단계가 실패하더라도 '토큰 만료'로 잘못 보고되지 않습니다.
    try:
        c = _client()
        me = c.whoami()
    except Exception:
        _emit("token_ok", "false")
        raise
    _emit("token_ok", "true")

    log.info("인증 방식 : %s (%s)", c.auth, c.host)
    # 게시에 실제로 쓰는 ID를 보여줍니다.
    # 응답에 섞여 오는 다른 식별자를 찍으면 값이 달라 보여 혼란을 줍니다.
    log.info("연결 OK   : @%s (게시용 ID=%s)", me.get("username"), c.user_id)
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
    paths = _render().render_post(post, book, outdir)

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


def last_posted_week(state: dict, posts: list) -> int:
    """가장 최근에 인스타에 올라간 회차 번호. 기록이 없으면 다음 예정 회차."""
    hist = state.get("history", [])
    if hist:
        return hist[-1]["week"]
    idx = state.get("next_index", 0) % len(posts)
    return posts[idx]["week"]


def build_reels_texts(post: dict, book: dict) -> dict:
    cfg = book["reels"]
    tags = (book["base_hashtags"] + post.get("hashtags", []))[: cfg.get("max_hashtags", 8)]
    tag_str = " ".join(tags)
    return {
        "caption": cfg["caption_template"].format(
            hook=post["hook"], summary=post["summary"], hashtags=tag_str),
        "shorts_title": cfg["shorts_title"].format(hook=post["hook"]),
        "shorts_description": cfg["shorts_description"].format(
            summary=post["summary"], buy_url=book["buy_url"], hashtags=tag_str),
    }


def _linkedin_cfg() -> dict:
    with open(os.path.join(CONTENT, "linkedin.json"), encoding="utf-8") as f:
        return json.load(f)


def _linkedin_body(post: dict, cfg: dict, slides: int) -> str:
    """카드 문안을 링크드인 본문으로 조립한다.

    posts.json 해당 회차에 'linkedin_body' 가 있으면 그 글을 그대로 씁니다.
    직접 길게 쓴 원고를 넣고 싶을 때 쓰는 자리입니다.
    """
    hand = (post.get("linkedin_body") or "").strip()
    if hand:
        return hand

    q = cfg["questions"].get(str(post["week"]), "")
    lines = [post["hook"], "", post["summary"], ""]

    lead = cfg.get("lead_in", "").strip()
    if lead:
        lines += [lead, ""]

    for slide in post["slides"][:slides]:
        lines.append(f"▸ {slide['title']}")
        lines.append(" ".join(slide["body"]))
        lines.append("")

    if q:
        lines.append(q)
    return "\n".join(lines).strip()


def build_linkedin_texts(post: dict, book: dict) -> dict:
    cfg = _linkedin_cfg()
    tags = " ".join(cfg["hashtags"] + post.get("hashtags", [])[:2])
    tail = f"\n\n{cfg['cta']}\n\n{tags}"

    return {
        # PDF를 함께 올릴 때: 카드가 내용을 대신하므로 항목을 줄인다
        "short": _linkedin_body(post, cfg, cfg.get("carousel_slides", 3)) + tail,
        # 텍스트 위주로 올릴 때: 5장을 모두 풀어 쓴다
        "full": _linkedin_body(post, cfg, cfg.get("max_body_slides", 5)) + tail,
        "first_comment": cfg["first_comment"].format(buy_url=book["buy_url"]),
    }


def cmd_pack(args):
    """메일로 보낼 꾸러미(영상/PDF + 복붙용 문구)를 만든다."""
    book, posts = load()
    state = load_state()

    week = args.week or last_posted_week(state, posts)
    post = next(p for p in posts if p["week"] == week)
    outdir = os.path.join(ROOT, "output", "email")
    os.makedirs(outdir, exist_ok=True)

    def w(name: str, text: str) -> str:
        p = os.path.join(outdir, name)
        with open(p, "w", encoding="utf-8") as f:
            # 반드시 줄바꿈으로 끝내야 합니다.
            # GitHub Actions 가 이 파일을 여러 줄 환경변수로 읽을 때,
            # 마지막 줄에 줄바꿈이 없으면 종료 표시가 본문에 달라붙어 실패합니다.
            f.write(text.rstrip("\n") + "\n")
        return p

    if args.kind == "reels":
        import video
        cards = _render().render_post(
            post, book, os.path.join(CARDS_DIR, f"w{week:02d}"))
        mp4 = os.path.join(outdir, f"w{week:02d}_reels.mp4")
        video.build(cards, mp4)

        t = build_reels_texts(post, book)
        w(f"w{week:02d}_릴스_문구.txt",
          f"[인스타 릴스 캡션]\n\n{t['caption']}\n\n\n"
          f"[유튜브 쇼츠 제목]\n\n{t['shorts_title']}\n\n\n"
          f"[유튜브 쇼츠 설명]\n\n{t['shorts_description']}\n")

        body = "\n".join([
            f"{week}회차 「{post['chapter']}」 인스타 게시가 끝났습니다.",
            "",
            "릴스는 본 게시물과 시차가 적을수록 좋으니 오늘 안에 올려주세요.",
            "첨부한 mp4를 올리면서 앱에서 유행하는 음악을 얹으면 도달이 훨씬 좋아집니다.",
            "",
            "─────────────────────",
            "■ 인스타 릴스 캡션 (복붙)",
            "─────────────────────",
            "",
            t["caption"],
            "",
            "─────────────────────",
            "■ 유튜브 쇼츠 제목",
            "─────────────────────",
            "",
            t["shorts_title"],
            "",
            "─────────────────────",
            "■ 유튜브 쇼츠 설명",
            "─────────────────────",
            "",
            t["shorts_description"],
            "",
            "─────────────────────",
            "첨부: 릴스·쇼츠용 세로 영상(mp4, 무음), 같은 내용의 문구 파일",
        ])
        w("body_reels.txt", body)
        log.info("\n릴스 꾸러미 준비 완료 (%d회차) → %s", week, outdir)

    else:  # linkedin
        import carousel_pdf
        cards = _render().render_post(
            post, book,
            os.path.join(ROOT, "output", "linkedin_cards", f"w{week:02d}"),
            cta_variant="linkedin")
        pdf = os.path.join(outdir, f"w{week:02d}_linkedin.pdf")
        carousel_pdf.build(cards, pdf,
                           title=f"{book['title']} — {post['chapter']}",
                           author=book["author"])

        t = build_linkedin_texts(post, book)
        w(f"w{week:02d}_링크드인_본문.txt",
          f"[A. PDF와 함께 올릴 짧은 본문 — 권장]\n\n{t['short']}\n\n\n"
          f"[B. 표지 1장과 올릴 전체 본문]\n\n{t['full']}\n\n\n"
          f"[게시 직후 직접 달 첫 댓글]\n\n{t['first_comment']}\n")

        # 인스타 게시가 실패한 주에는 같은 회차가 또 오게 됩니다.
        # 중복 게시를 막기 위해 언제 올라간 회차인지 알려줍니다.
        stale = ""
        for h in reversed(state.get("history", [])):
            if h.get("week") != week or not h.get("posted_at"):
                continue
            try:
                d = datetime.fromisoformat(h["posted_at"])
                days = (datetime.now(timezone.utc) - d).days
            except ValueError:
                break
            if days >= 3:
                stale = (
                    f"⚠️ 이 회차는 {days}일 전에 인스타에 올라간 것입니다.\n"
                    "   이번 주 인스타 게시가 실패했을 수 있습니다.\n"
                    "   이미 링크드인에 올리셨다면 이 메일은 건너뛰세요.\n"
                )
            break

        body = "\n".join([
            f"오늘 링크드인에 올릴 {week}회차 「{post['chapter']}」 입니다.",
            "",
            *([stale, ""] if stale else []),
            "1. 링크드인 글쓰기 → 첨부 아이콘 → '문서 추가' 로 첨부 PDF 업로드",
            "2. 아래 본문을 복사해 붙여넣기",
            "3. 게시 직후 맨 아래 '첫 댓글'을 직접 댓글로 등록",
            "   (본문에 외부 링크를 넣으면 링크드인이 도달을 줄입니다)",
            "",
            "─────────────────────",
            "■ 본문 (복붙)",
            "─────────────────────",
            "",
            t["full"],
            "",
            "─────────────────────",
            "■ 게시 직후 달 첫 댓글",
            "─────────────────────",
            "",
            t["first_comment"],
            "",
            "─────────────────────",
            "첨부: 카드 7장 PDF(캐러셀용), 같은 내용의 문구 파일",
            "PDF 마지막 장은 '첫 번째 댓글에 링크를 남겼습니다' 로 되어 있습니다.",
        ])
        w("body_linkedin.txt", body)
        log.info("\n링크드인 꾸러미 준비 완료 (%d회차) → %s", week, outdir)

    _emit("week", week)
    _emit("chapter", post["chapter"])


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
        "## 두 가지 방식 중 고르세요",
        "",
        "| | A. 카드 PDF + 짧은 글 (권장) | B. 긴 글 + 표지 1장 |",
        "|---|---|---|",
        "| 첨부 | `wNN_linkedin.pdf` (7쪽 캐러셀) | `wNN_01_cover.png` 1장 |",
        "| 본문 | 짧은 버전 | 전체 버전 |",
        "| 특징 | 넘겨보는 만큼 체류 시간이 길어 도달이 좋음 | 검색·복사에 유리 |",
        "",
        "**A가 유리한 이유**: 링크드인은 PDF를 올리면 좌우로 넘기는 캐러셀로 보여줍니다.",
        "카드가 내용을 대신하므로 본문은 짧게 두는 편이 읽힙니다.",
        "PDF는 `output/pdf/` 에 있거나, Actions 실행 결과의 Artifacts 에서 내려받을 수 있습니다.",
        "",
        "## 게시 순서",
        "",
        "1. 링크드인 글쓰기 → 첨부 아이콘 → **문서 추가** 로 PDF 업로드 (A 방식)",
        "2. 아래 본문을 복사해 붙여넣기",
        "3. 게시 후 **바로 「첫 댓글」을 직접 댓글로 등록**",
        "   (본문에 외부 링크를 넣으면 링크드인이 도달을 줄입니다)",
        "4. 댓글이 달리면 되도록 답글을 남기세요. 추가 노출로 이어집니다.",
        "",
        "---",
        "",
        "## 첫 댓글 (24편 공통)",
        "",
        "```",
        cfg["first_comment"].format(buy_url=book["buy_url"]),
        "```",
        "",
        "---",
        "",
    ]

    for post in posts:
        t = build_linkedin_texts(post, book)
        hand = "  *(직접 쓰신 원고를 사용)*" if post.get("linkedin_body") else ""

        out += [
            f"## {post['week']}회차 — {post['chapter']}{hand}",
            "",
            f"**첨부**: `w{post['week']:02d}_linkedin.pdf` (A) 또는 "
            f"`w{post['week']:02d}_01_cover.png` (B)",
            "",
            f"### A. 긴 본문 — 메일로 보내드리는 기본값 ({len(t['full'])}자)",
            "",
            "```",
            t["full"],
            "```",
            "",
            f"### B. 짧은 본문 — PDF가 내용을 대신할 때 ({len(t['short'])}자)",
            "",
            "```",
            t["short"],
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
        cards = _render().render_post(
            post, book, os.path.join(CARDS_DIR, f"w{post['week']:02d}"))
        out = os.path.join(outdir, f"w{post['week']:02d}_reels.mp4")
        video.build(cards, out)
        made.append(out)
        log.info("  WEEK %02d · %s", post["week"], post["chapter"])

    log.info("\n%d개 영상을 %s 에 저장했습니다.", len(made), outdir)
    log.info("업로드할 때 앱에서 유행하는 음악을 직접 얹으면 도달이 훨씬 좋아집니다.")


def cmd_pdf(args):
    """링크드인 문서 캐러셀용 PDF 생성."""
    import carousel_pdf

    book, posts = load()
    state = load_state()
    targets = posts if args.all else [pick_post(posts, state, args.week)]

    outdir = os.path.join(ROOT, "output", "pdf")
    for post in targets:
        # 링크드인용은 마지막 장을 '첫 댓글에 링크' 문구로 바꿔 따로 렌더링합니다.
        cards = _render().render_post(
            post, book,
            os.path.join(ROOT, "output", "linkedin_cards", f"w{post['week']:02d}"),
            cta_variant="linkedin")
        out = os.path.join(outdir, f"w{post['week']:02d}_linkedin.pdf")
        carousel_pdf.build(
            cards, out,
            title=f"{book['title']} — {post['chapter']}",
            author=book["author"],
        )
        log.info("  WEEK %02d · %s", post["week"], post["chapter"])

    log.info("\nPDF를 %s 에 저장했습니다.", outdir)
    log.info("링크드인 글쓰기 → 첨부 아이콘 → '문서 추가' 로 올리면 캐러셀이 됩니다.")


def cmd_threads(args):
    book, posts = load()
    state = load_state()
    post = pick_post(posts, state, args.week, key="threads_next_index")

    # 표지 카드를 루트 포스트 이미지로 쓰기 위해 렌더링
    outdir = os.path.join(CARDS_DIR, f"w{post['week']:02d}")
    paths = _render().render_post(post, book, outdir)
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

    pk = sub.add_parser("pack", help="메일로 보낼 꾸러미(영상/PDF + 문구) 준비")
    pk.add_argument("--kind", choices=["reels", "linkedin"], required=True)
    pk.add_argument("--week", type=int)
    pk.set_defaults(func=cmd_pack)

    v = sub.add_parser("video", help="릴스·쇼츠용 세로 영상(mp4) 생성")
    v.add_argument("--week", type=int)
    v.add_argument("--all", action="store_true")
    v.set_defaults(func=cmd_video)

    pf = sub.add_parser("pdf", help="링크드인 문서 캐러셀용 PDF 생성")
    pf.add_argument("--week", type=int)
    pf.add_argument("--all", action="store_true")
    pf.set_defaults(func=cmd_pdf)

    t = sub.add_parser("threads", help="스레드에 3연속 포스트로 게시")
    t.add_argument("--week", type=int)
    t.add_argument("--dry-run", action="store_true")
    t.set_defaults(func=cmd_threads)

    args = ap.parse_args()
    try:
        args.func(args)
    except Exception as e:  # 스택 트레이스 대신 읽을 수 있는 메시지로
        # threads.py 처럼 안 쓰는 모듈은 올리지 않았을 수 있으므로
        # 없으면 조용히 건너뜁니다. (없다고 여기서 죽으면 진짜 원인이 가려집니다)
        for mod_name, cls_name, doc in (
            ("instagram", "InstagramError", "docs/SETUP.md"),
            ("threads", "ThreadsError", "docs/SETUP_THREADS.md"),
        ):
            try:
                err_cls = getattr(importlib.import_module(mod_name), cls_name)
            except Exception:
                continue
            if isinstance(e, err_cls):
                log.error("\n❌ %s\n\n해결 방법은 %s 의 '문제 해결' 표를 확인하세요.", e, doc)
                sys.exit(1)
        raise


if __name__ == "__main__":
    main()
