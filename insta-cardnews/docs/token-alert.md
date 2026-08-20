## 갱신 방법 — 둘 중 편한 쪽으로

### 방법 A. Meta 콘솔에서 새로 발급 (파이썬 없어도 됨)

1. [Meta 개발자 콘솔](https://developers.facebook.com/apps/) → 내 앱 (`cardnews-bot`)
2. 좌측 **Instagram** → **Instagram 로그인이 포함된 API 설정**
3. **「1. 액세스 토큰 생성」** → 계정 옆의 토큰을 복사
   (없으면 **계정 추가** 로 다시 연결)
4. GitHub → **Settings → Secrets and variables → Actions → `IG_ACCESS_TOKEN` → Update**
5. `state.json` 의 `token_issued_at` 을 **오늘 날짜**로 수정 후 커밋

> 복사한 값에 눈에 안 보이는 공백이 섞여 있을 수 있습니다.
> 붙여넣은 뒤 커서를 맨 뒤로 옮겨 여백이 없는지 확인하세요.

### 방법 B. 명령어로 60일 연장 (파이썬이 있는 경우)

```bash
export IG_ACCESS_TOKEN="현재_토큰"
python src/main.py refresh
```

출력된 새 토큰을 위 4~5번과 똑같이 반영하면 됩니다.
단, **이미 만료된 토큰은 연장되지 않습니다.** 그때는 방법 A로 가세요.

---

## 갱신한 뒤 확인

Actions → **토큰 상태 점검** → **Run workflow**

로그에 `연결 OK : @계정명` 이 보이면 정상입니다.

---

확인이 끝나면 **이 이슈를 닫아주세요.**
열려 있는 동안에는 다음 알림이 올라오지 않습니다.
