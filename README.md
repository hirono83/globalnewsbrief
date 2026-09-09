# 글로벌 뉴스 브리핑 엔진

Codex가 10개 뉴스 카테고리를 실시간 검색해 한국어 브리핑을 만들고, 매일 `reports/YYYY-MM-DD.md`에 커밋합니다.

## 설치

1. 이 폴더의 내용을 GitHub 리포지토리 기본 브랜치에 올립니다.
2. OpenAI Platform에서 API 키를 준비합니다.
3. GitHub 리포지토리의 **Settings → Secrets and variables → Actions**에서 `OPENAI_API_KEY`라는 Repository secret을 추가합니다.
4. **Actions → Daily global news briefing → Run workflow**에서 한 번 수동 실행해 확인합니다. 필요하면 `focus`에 더 깊게 볼 카테고리를 입력합니다.

이후 매일 오전 7시(Asia/Seoul)에 자동 실행됩니다. 예약 워크플로는 기본 브랜치에서만 실행됩니다.

## 결과와 재실행

- 결과: `reports/YYYY-MM-DD.md`
- 같은 날 다시 실행하면 같은 파일을 최신 브리핑으로 갱신합니다.
- 결과가 이전 실행과 같으면 새 커밋을 만들지 않습니다.
- 고정 출력 구조나 10개 카테고리 중 하나가 빠지면 검증 단계에서 실패하며 보고서를 커밋하지 않습니다.

## 권한·비용

워크플로에는 보고서 커밋에 필요한 `contents: write`만 부여되어 있습니다. 조직 정책이나 브랜치 보호 규칙이 자동 커밋을 막으면 별도 보고서 브랜치 또는 Pull Request 방식으로 바꿔야 합니다.

Codex 실행과 실시간 웹 검색은 `OPENAI_API_KEY`에 API 사용료가 청구됩니다. 기본 모델은 `gpt-5.5`, 추론 강도는 `high`입니다. 비용이나 실행 시간이 부담되면 `.github/workflows/daily-briefing.yml`의 `effort`를 `medium`으로 낮출 수 있습니다.

## 근거 문서

- [Codex GitHub Action](https://learn.chatgpt.com/docs/github-action)
- [Codex 웹 검색](https://learn.chatgpt.com/ko-KR/docs/web-search)
- [GitHub Actions 워크플로 문법](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
