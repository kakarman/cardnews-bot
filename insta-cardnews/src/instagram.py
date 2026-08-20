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

# 아래 오류들은 우리 잘못이 아니라 Meta 쪽이 잠깐 불안정한 경우입니다.
# 잠시 뒤 다시 시도하면 대부분 그냥 성공합니다.
RETRY_HTTP = {429, 500, 502, 503, 504}
RETRY_CODES = {
    1,    # An unknown error occurred
    2,    # An unexpected error has occurred  ← 가장 흔함
    4,    # Application request limit reached
    17,   # User request limit reached
    32,   # Page request limit reached
    341,  # Application limit reached
    613,  # Calls to this api have exceeded the rate limit
}
MAX_RETRIES = 5
RETRY_BASE_DELAY = 8  # 초. 시도할수록 8 → 16 → 24 … 로 늘어납니다


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
    def _once(self, method: str, url: str, params: dict):
        r = requests.request(method, url, data=params if method == "POST" else None,
                             params=None if method == "POST" else params,
                             timeout=self.timeout)
        try:
            return r, r.json()
        except ValueError:
            return r, {"error": {"message": r.text[:300], "type": "NonJSON"}}

    def _call(self, method: str, path: str, retry: bool = True, **params) -> dict:
        """retry=False 는 '두 번 실행되면 안 되는 요청'에 씁니다(예: 발행)."""
        params["access_token"] = self.token
        url = f"{self.base}/{path.lstrip('/')}"
        last = ""

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r, data = self._once(method, url, params)
            except requests.RequestException as e:  # 네트워크 자체가 흔들린 경우
                r, data = None, {"error": {"message": str(e), "type": "Network"}}

            if r is not None and r.status_code < 400 and "error" not in data:
                return data

            err = data.get("error", {})
            code = err.get("code")
            status = r.status_code if r is not None else 0
            last = (
                f"Graph API 오류 [{status}] {err.get('type')} / code={code} "
                f"subcode={err.get('error_subcode')}\n"
                f"  message: {err.get('message')}\n"
                f"  요청: {method} {url}"
            )

            transient = (status in RETRY_HTTP or code in RETRY_CODES
                         or err.get("type") in {"Network", "NonJSON"})
            if retry and transient and attempt < MAX_RETRIES:
                wait = RETRY_BASE_DELAY * attempt
                log.warning(
                    "  Meta 쪽 일시적 오류(code=%s). %d초 뒤 다시 시도합니다 (%d/%d)",
                    code, wait, attempt, MAX_RETRIES - 1)
                time.sleep(wait)
                continue

            if transient and not retry:
                last += ("\n\n  ⚠️ 이 요청은 두 번 실행되면 중복 게시가 될 수 있어"
                         "\n     자동 재시도를 하지 않았습니다."
                         "\n     인스타그램을 확인해 보시고, 올라간 게 없으면"
                         "\n     Actions 에서 워크플로를 다시 실행하세요.")
            elif transient:
                last += ("\n\n  ⚠️ Meta 쪽 일시적 오류가 계속되고 있습니다."
                         f"\n     {MAX_RETRIES - 1}번 재시도했지만 실패했습니다."
                         "\n     보통 몇십 분 뒤에는 정상으로 돌아옵니다."
                         "\n     Actions 에서 워크플로를 다시 실행해 주세요.")
            raise InstagramError(last)

        raise InstagramError(last)

    def preflight(self, urls: list[str], tries: int = 6, delay: int = 10) -> None:
        """인스타에 넘기기 전에 이미지 URL이 실제로 열리는지 확인한다.

        GitHub 에 방금 올린 이미지가 CDN 에 아직 안 퍼졌을 때,
        Meta 가 내뱉는 알 수 없는 오류 대신 명확한 메시지를 보기 위함입니다.
        """
        for url in urls:
            for i in range(1, tries + 1):
                try:
                    r = requests.head(url, timeout=20, allow_redirects=True)
                    ok = r.status_code == 200 and \
                        r.headers.get("content-type", "").startswith("image/")
                    if ok:
                        break
                    reason = f"HTTP {r.status_code}, type={r.headers.get('content-type')}"
                except requests.RequestException as e:
                    reason = str(e)
                if i < tries:
                    log.info("  이미지 아직 준비 안 됨(%s). %d초 대기 (%d/%d)",
                             reason, delay, i, tries)
                    time.sleep(delay)
            else:
                raise InstagramError(
                    f"이미지 URL을 열 수 없습니다:\n  {url}\n  ({reason})\n\n"
                    "  · 저장소가 Private 이면 인스타그램이 이미지를 못 가져옵니다. "
                    "Public 인지 확인하세요.\n"
                    "  · 이미지 커밋이 정상적으로 push 됐는지 Actions 로그를 확인하세요."
                )
        log.info("  이미지 %d장 모두 접근 가능 확인", len(urls))

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
        # 발행은 두 번 실행되면 같은 글이 두 번 올라갈 수 있어
        # 자동 재시도를 하지 않습니다.
        res = self._call("POST", f"{self.user_id}/media_publish",
                         retry=False, creation_id=creation_id)
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

        log.info("0/4 이미지 URL 접근 확인")
        self.preflight(image_urls)

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
