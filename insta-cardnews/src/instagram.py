"""Instagram Graph API 캐러셀 게시 클라이언트.

흐름:
  1) 이미지 7장 각각을 '캐러셀 아이템 컨테이너'로 만든다
  2) 그 아이템들을 묶어 '캐러셀 컨테이너'를 만든다 (캡션 포함)
  3) 컨테이너가 FINISHED 될 때까지 기다렸다가 발행한다
  4) 발행된 게시물에 첫 댓글로 구매 링크를 단다

주의: 이미지는 반드시 공개적으로 접근 가능한 URL이어야 합니다.
      (로컬 파일 업로드는 API가 지원하지 않습니다)

인증 방식이 두 가지라 호스트가 다릅니다 (IG_AUTH_MODE 로 선택):
  instagram : Instagram 로그인  → graph.instagram.com  (페이스북 페이지 불필요, 권장)
  facebook  : Facebook 로그인   → graph.facebook.com   (페이스북 페이지 연결 필요)
"""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

HOSTS = {
    "instagram": "https://graph.instagram.com",
    "facebook": "https://graph.facebook.com",
}
DEFAULT_VERSION = "v23.0"
DEFAULT_AUTH = "instagram"


class InstagramError(RuntimeError):
    pass


class InstagramClient:
    def __init__(self, user_id: str, access_token: str,
                 version: str = DEFAULT_VERSION, timeout: int = 60,
                 auth: str = DEFAULT_AUTH):
        if not access_token:
            raise InstagramError(
                "IG_ACCESS_TOKEN 이 비어 있습니다. "
                "GitHub Secrets 또는 .env 설정을 확인하세요."
            )
        auth = (auth or DEFAULT_AUTH).strip().lower()
        if auth not in HOSTS:
            raise InstagramError(
                f"IG_AUTH_MODE 는 'instagram' 또는 'facebook' 이어야 합니다 (받은 값: {auth})"
            )
        if auth == "facebook" and not user_id:
            raise InstagramError(
                "Facebook 로그인 방식에서는 IG_USER_ID 가 반드시 필요합니다."
            )
        self.auth = auth
        self.host = HOSTS[auth]
        self.token = access_token.strip()
        self.base = f"{self.host}/{version}"
        self.timeout = timeout
        # Instagram 로그인 방식은 ID를 비워두면 토큰에서 자동으로 찾아옵니다.
        self.user_id = str(user_id).strip() if user_id else ""
        if not self.user_id:
            self.user_id = self._resolve_self_id()

    # ── 저수준 ──────────────────────────────────────────
    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        url = f"{self.base}/{path.lstrip('/')}"
        r = requests.request(method, url, data=params if method == "POST" else None,
                             params=None if method == "POST" else params,
                             timeout=self.timeout)
        try:
            data = r.json()
        except ValueError:
            raise InstagramError(f"응답을 해석할 수 없습니다 ({r.status_code}): {r.text[:300]}")
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            raise InstagramError(
                f"Graph API 오류 [{r.status_code}] "
                f"{err.get('type')} / code={err.get('code')} "
                f"subcode={err.get('error_subcode')}\n"
                f"  message: {err.get('message')}\n"
                f"  요청: {method} {url}"
            )
        return data

    # ── 공개 메서드 ─────────────────────────────────────
    def _resolve_self_id(self) -> str:
        """Instagram 로그인 토큰에서 내 인스타 계정 ID를 알아낸다."""
        res = self._call("GET", "me", fields="user_id,username")
        uid = res.get("user_id") or res.get("id")
        if not uid:
            raise InstagramError(
                "토큰에서 인스타그램 계정 ID를 찾지 못했습니다. "
                "IG_USER_ID 를 직접 설정해 주세요."
            )
        log.info("계정 ID 자동 확인: %s (@%s)", uid, res.get("username", "?"))
        return str(uid)

    def whoami(self) -> dict:
        """토큰과 계정 ID가 살아 있는지 확인 (실제 게시 전 점검용)."""
        fields = "user_id,username" if self.auth == "instagram" else "id,username,name"
        res = self._call("GET", self.user_id, fields=fields)
        res.setdefault("id", res.get("user_id", self.user_id))
        return res

    def refresh_token(self) -> dict:
        """Instagram 로그인 장기 토큰을 60일 더 연장한다.

        조건: 최소 24시간 이상 지났고 아직 만료되지 않은 장기 토큰이어야 합니다.
        """
        if self.auth != "instagram":
            raise InstagramError(
                "이 명령은 Instagram 로그인 방식에서만 동작합니다.\n"
                "Facebook 로그인 방식은 액세스 토큰 디버거에서 수동으로 연장하세요."
            )
        params = {"grant_type": "ig_refresh_token", "access_token": self.token}
        r = requests.get(f"{self.host}/refresh_access_token", params=params,
                         timeout=self.timeout)
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            raise InstagramError(f"토큰 연장 실패: {data}")
        return data

    def create_item(self, image_url: str) -> str:
        res = self._call("POST", f"{self.user_id}/media",
                         image_url=image_url, is_carousel_item="true")
        return res["id"]

    def create_carousel(self, children: list[str], caption: str) -> str:
        res = self._call("POST", f"{self.user_id}/media",
                         media_type="CAROUSEL",
                         children=",".join(children),
                         caption=caption)
        return res["id"]

    def wait_ready(self, container_id: str, tries: int = 30, delay: int = 4) -> None:
        """컨테이너가 FINISHED 될 때까지 폴링. ERROR면 즉시 중단."""
        for i in range(tries):
            res = self._call("GET", container_id, fields="status_code,status")
            code = res.get("status_code")
            if code == "FINISHED":
                return
            if code == "ERROR":
                raise InstagramError(
                    f"컨테이너 처리 실패: {res.get('status')}\n"
                    "  이미지 URL이 공개 접근 가능한지, 형식이 JPEG/PNG인지 확인하세요."
                )
            log.info("  컨테이너 %s 상태=%s (%d/%d)", container_id, code, i + 1, tries)
            time.sleep(delay)
        raise InstagramError(f"컨테이너 {container_id} 가 제한 시간 안에 준비되지 않았습니다.")

    def publish(self, creation_id: str) -> str:
        res = self._call("POST", f"{self.user_id}/media_publish",
                         creation_id=creation_id)
        return res["id"]

    def comment(self, media_id: str, message: str) -> str:
        """자기 게시물에 댓글을 단다. instagram_manage_comments 권한 필요."""
        res = self._call("POST", f"{media_id}/comments", message=message)
        return res["id"]

    def permalink(self, media_id: str) -> str:
        return self._call("GET", media_id, fields="permalink").get("permalink", "")

    # ── 한 방에 처리 ────────────────────────────────────
    def post_carousel(self, image_urls: list[str], caption: str,
                      first_comment: str | None = None) -> dict:
        if not 2 <= len(image_urls) <= 10:
            raise InstagramError(
                f"캐러셀은 2~10장이어야 합니다 (현재 {len(image_urls)}장)."
            )

        log.info("1/4 아이템 컨테이너 %d개 생성", len(image_urls))
        children = []
        for i, url in enumerate(image_urls, 1):
            cid = self.create_item(url)
            log.info("  [%d/%d] %s ← %s", i, len(image_urls), cid, url)
            children.append(cid)

        log.info("2/4 캐러셀 컨테이너 생성")
        carousel_id = self.create_carousel(children, caption)

        log.info("3/4 처리 완료 대기")
        self.wait_ready(carousel_id)

        log.info("4/4 발행")
        media_id = self.publish(carousel_id)
        result = {"media_id": media_id, "permalink": self.permalink(media_id)}

        if first_comment:
            try:
                # 발행 직후에는 댓글이 거부될 수 있어 잠깐 대기
                time.sleep(5)
                result["comment_id"] = self.comment(media_id, first_comment)
                log.info("  첫 댓글 등록 완료: %s", result["comment_id"])
            except InstagramError as e:
                # 댓글 실패가 게시 자체를 실패로 만들면 안 됨
                log.error("  첫 댓글 등록 실패(게시는 정상): %s", e)
                result["comment_error"] = str(e)

        return result
