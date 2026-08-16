## 갱신 방법 (1분)

내 PC에서 프로젝트 폴더를 열고:

```bash
export IG_ACCESS_TOKEN="현재_토큰"
python src/main.py refresh
```

출력된 내용대로 두 가지만 하시면 끝입니다.

1. 새 토큰을 **Settings → Secrets and variables → Actions → `IG_ACCESS_TOKEN`** 에서 **Update**
2. `state.json` 의 `token_issued_at` 을 **오늘 날짜**로 수정 후 커밋

## refresh 가 안 될 때

토큰이 이미 만료됐다면 연장이 불가능합니다. 새로 발급받으세요.

[Meta 개발자 콘솔](https://developers.facebook.com/apps/) → 내 앱 →
**Instagram → Instagram 로그인이 포함된 API 설정 → 1. 액세스 토큰 생성**

발급 후 위 1~2번을 똑같이 진행하면 됩니다.

---
갱신을 마치면 **이 이슈를 닫아주세요.** 닫아야 다음 알림이 정상적으로 올라옵니다.
