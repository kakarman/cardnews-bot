#!/usr/bin/env python3
"""Gmail 앱 비밀번호가 실제로 동작하는지 10초 만에 확인하는 도구.

GitHub Actions 에 넣기 전에 내 PC에서 먼저 돌려보면
'535 Username and Password not accepted' 의 원인을 바로 알 수 있습니다.

사용법:

    python tools/test_mail.py

실행하면 주소와 앱 비밀번호를 물어봅니다.
(입력한 값은 화면에도, 파일에도 남지 않습니다)
"""

from __future__ import annotations

import getpass
import re
import smtplib
import ssl
import sys
from email.message import EmailMessage

HOST = "smtp.gmail.com"

# 구글 앱 비밀번호 화면은 'abcd efgh ijkl mnop' 처럼 보이지만,
# 실제로는 일반 공백이 아니라 줄바꿈 없는 공백(U+00A0)을 씁니다.
# 그대로 복사하면 눈에 안 보이는 문자가 섞여 들어가 SMTP 로그인이 실패합니다.
# 아래 정규식으로 모든 종류의 공백과 폭 없는 문자를 싹 걷어냅니다.
INVISIBLE = re.compile(r"[\s ​‌‍⁠﻿]+")


def clean_pw(raw: str) -> str:
    return INVISIBLE.sub("", raw or "")


def diagnose(user: str, pw_raw: str) -> list[str]:
    """보내기 전에 눈에 보이는 문제부터 걸러낸다."""
    warns = []

    if "@" not in user:
        warns.append(
            "MAIL_USERNAME 이 이메일 주소가 아닙니다. "
            "'saaad26' 이 아니라 'saaad26@gmail.com' 처럼 전체 주소여야 합니다."
        )
    if user != user.strip():
        warns.append("주소 앞뒤에 공백이 붙어 있습니다.")

    pw = clean_pw(pw_raw)
    if pw_raw != pw_raw.strip():
        warns.append("앱 비밀번호 앞뒤에 공백이나 줄바꿈이 붙어 있습니다. (가장 흔한 원인)")
    if len(pw) != 16:
        warns.append(
            f"앱 비밀번호가 16자가 아닙니다 (공백 제외 {len(pw)}자). "
            "평소 쓰는 구글 비밀번호를 넣으신 건 아닌지 확인하세요."
        )
    elif not re.fullmatch(r"[a-z]{16}", pw):
        warns.append(
            "앱 비밀번호는 보통 소문자 알파벳 16자입니다. "
            "다른 문자가 섞여 있다면 잘못 복사한 것일 수 있습니다."
        )
    return warns


def try_login(user: str, pw: str, port: int) -> tuple[bool, str]:
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(HOST, 465, context=ctx, timeout=20) as s:
                s.login(user, pw)
        else:
            with smtplib.SMTP(HOST, 587, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(user, pw)
        return True, ""
    except smtplib.SMTPAuthenticationError as e:
        return False, f"{e.smtp_code} {e.smtp_error.decode('utf-8', 'ignore')[:200]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def send_test(user: str, pw: str, to: str, port: int) -> None:
    msg = EmailMessage()
    msg["Subject"] = "✅ 카드뉴스 봇 메일 설정 확인"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(
        "이 메일이 보이면 앱 비밀번호 설정이 정상입니다.\n"
        "같은 값을 GitHub Secrets 의 MAIL_USERNAME / MAIL_PASSWORD 에 넣으세요.\n"
    )
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(HOST, 465, context=ctx, timeout=20) as s:
            s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(HOST, 587, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(msg)


def describe(pw_raw: str) -> str:
    """비밀번호 자체는 절대 출력하지 않고, 모양만 설명한다."""
    stripped = pw_raw.strip()
    pw = clean_pw(pw_raw)
    bits = [f"길이 {len(pw)}자(공백 제외)"]
    if pw_raw != stripped:
        bits.append("⚠️ 앞뒤에 공백/줄바꿈 있음")
    if " " in stripped:
        bits.append("가운데 일반 공백 있음")
    weird = sorted({c for c in pw_raw if c != " " and INVISIBLE.fullmatch(c)})
    if weird:
        codes = ", ".join(f"U+{ord(c):04X}" for c in weird)
        bits.append(f"🔴 보이지 않는 특수 문자 {len(weird)}종 포함 ({codes})")
    kinds = []
    if any(c.islower() for c in pw):
        kinds.append("소문자")
    if any(c.isupper() for c in pw):
        kinds.append("대문자")
    if any(c.isdigit() for c in pw):
        kinds.append("숫자")
    if any(not c.isalnum() for c in pw):
        kinds.append("기호")
    bits.append("구성: " + "+".join(kinds) if kinds else "구성: 비어 있음")
    return " / ".join(bits)


def run_env() -> int:
    """환경변수로 실행 (GitHub Actions 용, 입력을 받지 않음)."""
    import os

    user = (os.environ.get("MAIL_USERNAME") or "").strip()
    pw_raw = os.environ.get("MAIL_PASSWORD") or ""
    to = (os.environ.get("MAIL_TO") or "").strip()
    pw = clean_pw(pw_raw)

    print("Gmail 설정 진단")
    print("─" * 46)
    print(f"MAIL_USERNAME : {'설정됨' if user else '❌ 비어 있음'}"
          f"{' / @ 포함' if '@' in user else ' / ❌ @ 없음(전체 주소가 아님)'}")
    print(f"MAIL_PASSWORD : {describe(pw_raw) if pw_raw else '❌ 비어 있음'}")
    print(f"MAIL_TO       : {'설정됨' if to else '❌ 비어 있음'}")
    print()

    if not user or not pw:
        print("❌ Secrets 가 비어 있습니다. 이름을 정확히 넣었는지 확인하세요.")
        return 1

    if len(pw) != 16:
        print(f"⚠️ 앱 비밀번호는 16자여야 하는데 {len(pw)}자입니다.")
        print("   평소 쓰는 구글 비밀번호를 넣으셨을 가능성이 높습니다.")
    elif not re.fullmatch(r"[a-z]{16}", pw):
        print("⚠️ 앱 비밀번호는 보통 소문자 16자입니다. 잘못 복사했을 수 있습니다.")

    for port in (465, 587):
        ok, err = try_login(user, pw, port)
        if ok:
            print(f"\n✅ 로그인 성공 (포트 {port})")
            if to:
                send_test(user, pw, to, port)
                print(f"   {to} 로 테스트 메일을 보냈습니다. 스팸함도 확인해 보세요.")
            if port == 587:
                print("\n   ※ 465가 막혀 있습니다. 워크플로의 server_port 를 587,")
                print("      secure 를 false 로 바꿔주세요.")
            return 0
        print(f"   포트 {port} 실패 → {err}")

    print("\n❌ 로그인 실패. 아래를 순서대로 확인하세요.\n")
    print("1. 앱 비밀번호를 '새로' 발급받으세요  ← 가장 확실합니다")
    print("   https://myaccount.google.com/apppasswords")
    print("   예전 것은 폐기됐거나 다른 계정 것일 수 있습니다.")
    print("   복사한 16자에서 공백을 모두 지우고 Secrets 에 붙여넣으세요.\n")
    print("2. 2단계 인증을 껐다 켠 적 있다면 기존 앱 비밀번호는 전부 무효입니다.")
    print("   https://myaccount.google.com/security\n")
    print("3. MAIL_USERNAME 과 앱 비밀번호가 '같은 구글 계정' 것인지 확인하세요.")
    return 1


def main() -> int:
    import os

    if os.environ.get("MAIL_USERNAME") or os.environ.get("MAIL_PASSWORD"):
        return run_env()

    print("\nGmail 앱 비밀번호 확인 도구")
    print("─" * 46)

    user = input("보내는 Gmail 주소 (MAIL_USERNAME): ").strip()
    pw_raw = getpass.getpass("앱 비밀번호 16자 (입력해도 화면에 안 보입니다): ")
    pw = clean_pw(pw_raw)

    warns = diagnose(user, pw_raw)
    if warns:
        print("\n⚠️  먼저 확인할 점")
        for w in warns:
            print(f"   · {w}")

    print("\n접속 시도 중…")
    for port in (465, 587):
        ok, err = try_login(user, pw, port)
        if ok:
            print(f"\n✅ 로그인 성공 (포트 {port})")
            if port == 587:
                print("   ※ 465가 막혀 있습니다. 워크플로의 server_port 를 587 로,")
                print("      secure 를 false 로 바꿔주세요.")
            to = input("\n테스트 메일을 받을 주소 (엔터 치면 건너뜀): ").strip()
            if to:
                send_test(user, pw, to, port)
                print(f"   {to} 로 보냈습니다. 메일함(스팸함 포함)을 확인하세요.")
            print("\n이 값을 그대로 GitHub Secrets 에 넣으면 됩니다.")
            print(f"   MAIL_USERNAME = {user}")
            print(f"   MAIL_PASSWORD = (방금 입력한 16자, 공백 없이)")
            return 0
        print(f"   포트 {port} 실패 → {err}")

    print("\n❌ 두 포트 모두 로그인에 실패했습니다.")
    print("""
아래를 순서대로 확인하세요.

1. 앱 비밀번호를 '새로' 발급받으세요  ← 가장 확실한 해결책
   https://myaccount.google.com/apppasswords
   예전에 만든 것은 폐기됐거나 다른 계정 것일 수 있습니다.

2. 2단계 인증이 켜져 있는지 확인
   https://myaccount.google.com/security
   2단계 인증을 껐다 켜면 기존 앱 비밀번호가 전부 무효가 됩니다.

3. 위 주소와 앱 비밀번호가 '같은 구글 계정' 것인지 확인

4. 회사·학교(Workspace) 계정이면 관리자가 SMTP를 막았을 수 있습니다.
   개인 Gmail 계정으로 시도해 보세요.
""")
    return 1


if __name__ == "__main__":
    sys.exit(main())
