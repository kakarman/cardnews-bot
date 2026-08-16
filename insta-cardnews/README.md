# 『IT기업에서 하루하루 어휴』 주간 카드뉴스 봇

책의 24개 꼭지를 **7장짜리 카드뉴스**로 자동 제작해
**매주 1회 인스타그램에 게시**하고, **첫 댓글에 YES24 구매 링크**를 자동으로 답니다.

한 번 세팅하면 **약 6개월(24주)간 손대지 않아도 돌아갑니다.**

---

## 어떻게 동작하나요

```
content/posts.json   ← 24주치 카드 문안 (여기만 고치면 내용이 바뀝니다)
        │
        ▼
   src/render.py      카드 7장을 PNG로 그림 (표지 + 본문5 + CTA)
        │
        ▼
   docs/cards/wNN/    GitHub에 커밋 → 공개 URL 확보
        │
        ▼
  src/instagram.py    캐러셀 게시 → 첫 댓글에 구매 링크
        │
        ▼
    state.json        다음 주엔 그다음 회차로 자동 진행
```

매주 화요일 밤 9시(한국시간)에 GitHub Actions가 위 과정을 알아서 실행합니다.

---

## 처음 시작하기

**👉 [docs/SETUP.md](docs/SETUP.md) 를 순서대로 따라 하세요.** (약 20~30분, 한 번만)

인스타 계정 전환부터 토큰 발급, GitHub 설정까지 화면 순서대로 적어두었습니다.

인증은 두 방식을 모두 지원합니다.

| 방식 | 호스트 | 페이스북 페이지 | 등록할 Secret |
|---|---|---|---|
| **Instagram 로그인** (기본·권장) | `graph.instagram.com` | 불필요 | `IG_ACCESS_TOKEN` 만 |
| Facebook 로그인 | `graph.facebook.com` | 필요 | `IG_ACCESS_TOKEN` + `IG_USER_ID` |

Instagram 로그인 방식에서는 계정 ID를 토큰에서 자동으로 찾아오고,
`python src/main.py refresh` 명령으로 토큰을 60일 연장할 수 있습니다.

---

## 카드 구성 (7장)

| 순서 | 역할 | 내용 |
|---|---|---|
| 1 | 표지 | 스크롤을 멈추게 하는 후킹 문구 |
| 2~6 | 본문 | 한 꼭지의 핵심을 5단계로 분해 |
| 7 | CTA | 구매 유도 + 프로필 링크 안내 |

규격은 1080×1350 (4:5)로, 인스타 피드에서 가장 크게 노출되는 세로 비율입니다.
톤은 책 표지의 차분한 화이트 계열을 그대로 가져왔습니다.

---

## 자주 하는 작업

### 카드만 미리 만들어 보기 (인스타 계정 없어도 됨)

```bash
pip install -r requirements.txt
python src/main.py render --all        # 24주치 전부 output/preview/ 에 생성
python src/main.py render --week 7     # 7회차만
```

### 실제로 올리지 않고 캡션·링크만 확인

```bash
python src/main.py post --dry-run
```

### 토큰·계정 연결 점검 / 토큰 연장

```bash
export IG_ACCESS_TOKEN="..."
python src/main.py check      # 어떤 계정에 연결됐는지 확인
python src/main.py refresh    # 토큰을 60일 더 연장 (Instagram 로그인 방식만)
```

### 특정 회차를 지금 바로 올리기

GitHub → **Actions** → **주간 카드뉴스 자동 게시** → **Run workflow**
→ `week` 칸에 회차 번호 입력 → 실행

(수동으로 특정 회차를 올려도 주간 자동 순서는 흐트러지지 않습니다)

---

## 내용 수정하기

### 카드 문구 바꾸기

`content/posts.json` 을 열어 해당 회차를 고치면 됩니다.

```json
{
  "week": 1,
  "chapter": "IT 업계의 그림자, 아웃소싱의 씁쓸한 현실",
  "hook": "캡션 첫 줄에 들어갈 한 문장",
  "cover": ["표지 1행", "표지 2행", "표지 3행"],
  "cover_sub": "표지 아래 작은 설명",
  "slides": [
    { "title": "소제목", "body": ["본문 1행", "본문 2행"] }
  ],
  "summary": "캡션 본문",
  "hashtags": ["#이번회차전용해시태그"]
}
```

- `cover` 는 **3행 이내**, `slides` 는 **5장 고정**, `body` 는 **4행 이내**를 권장합니다
- 길어져도 자동으로 줄바꿈되고 글자 크기가 줄어들지만, 짧을수록 잘 읽힙니다
- 저장 후 `python src/main.py render --week N` 으로 눈으로 확인해 보세요

### 회차를 추가하기

`posts.json` 배열 끝에 항목을 추가하고 `week` 번호만 이어서 붙이면 됩니다.
24주가 끝나면 자동으로 1회차부터 다시 돕니다 (`state.json` 의 `"loop": true`).
반복을 원치 않으면 `false` 로 바꾸세요.

### 공통 문구·해시태그 바꾸기

`content/book.json` 에서 CTA 카드 문구, 캡션 템플릿, 댓글 문구, 기본 해시태그를 관리합니다.

### 디자인 바꾸기

`src/theme.py` 의 색상과 글자 크기만 고치면 전체 카드에 반영됩니다.

### 게시 요일·시간 바꾸기

`.github/workflows/weekly-post.yml` 의 `cron` 값을 수정합니다. **UTC 기준**이라
원하는 한국시간에서 9시간을 빼면 됩니다.

```yaml
- cron: "0 12 * * 2"   # 화 21:00 KST
- cron: "0 11 * * 4"   # 목 20:00 KST
- cron: "0 0  * * 0"   # 일 09:00 KST
```

---

## 알아두면 좋은 것

**인스타그램에서 클릭되는 링크는 프로필 링크뿐입니다.**
캡션과 댓글의 URL은 파란 링크로 바뀌지 않습니다. 그래서 이 봇은
① 첫 댓글에 링크를 남겨 복사할 수 있게 하고
② 마지막 카드와 캡션에서 "프로필 링크"로 유도합니다.
**프로필에 YES24 링크를 반드시 걸어두세요.** (SETUP 9단계)

**API 제한은 넉넉합니다.** 하루 100건까지 게시 가능하고, 캐러셀 7장은 1건으로 계산됩니다.
주 1회는 전혀 문제되지 않습니다.

**유일한 정기 관리는 60일마다 토큰 갱신입니다.** SETUP 문서 맨 아래를 참고하세요.

---

## 파일 구조

```
├─ content/
│   ├─ book.json          책 정보 · CTA · 캡션 템플릿 · 기본 해시태그
│   └─ posts.json         24주치 카드 문안  ← 주로 여기를 수정
├─ src/
│   ├─ main.py            명령 진입점 (render / check / post)
│   ├─ render.py          카드 이미지 렌더링
│   ├─ theme.py           색·크기 등 디자인 토큰
│   ├─ fonts.py           한글 폰트 해석
│   └─ instagram.py       Graph API 게시 + 댓글
├─ docs/
│   ├─ SETUP.md           최초 세팅 가이드 ⭐
│   └─ cards/             생성된 카드 이미지 (공개 URL 원본)
├─ .github/workflows/
│   └─ weekly-post.yml    매주 자동 실행
└─ state.json             다음 회차 · 발행 기록
```
