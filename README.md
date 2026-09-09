# 글로벌 뉴스 브리핑 엔진 v4.0

10개 카테고리의 뉴스를 매일 수집·중복제거하고, 고유 사건 최대 20건으로 한국어
브리핑을 만들어 `reports/YYYY-MM-DD.md`에 저장하고 이메일·Notion으로 보낸다.

투자 자문·매수·매도 지시·목표가격·포지션 비중·수익 보장을 제공하지 않는다.

## 구조

수집과 판단을 분리한 하이브리드 파이프라인이다.

```
engine/collect.py       56개 피드 수집 → 구간 필터 → 정규화 → 1차 중복제거 → 사전정렬
engine/market_data.py   As-of가 확인된 가격 데이터 (미 국채 수익률 곡선)
        ↓  out/candidates.md · out/market_data.md
.claude/skills/news-brief   사건 단위 병합 → 사실 확인 → 중요도 선별 → 브리핑 작성
        ↓  reports/YYYY-MM-DD.md
engine/validate_report.py   고정 출력 구조와 행동 경계 검증
engine/check_links.py       링크가 실제로 존재하는지 검증
        ↓  커밋 · 이메일 · Notion
```

파이썬 단계는 **판단을 하지 않는다.** 검증 가능한 사실(어떤 기사가 언제 어느 매체에서
나왔는가)만 만들고, 중요도 평가와 사건 병합과 작성은 LLM 단계가 한다. 이렇게 나눈
이유는 세 가지다.

- 링크가 실재함이 보장된다. 모델이 그럴듯한 URL을 만들어낼 여지가 없다.
- Coverage Audit의 숫자(발견 기사 수, 카테고리별 검색 완료 여부)가 코드로 산출된다.
- 같은 입력으로 다시 돌려 결과를 재현하고 디버깅할 수 있다.

## 실행

외부 패키지 설치가 필요 없다. Python 3.11 이상만 있으면 된다.

```bash
python3 engine/collect.py          # 후보 수집 → out/candidates.md
python3 engine/market_data.py      # 가격 데이터 → out/market_data.md
```

브리핑 작성까지 한 번에 하려면 Claude Code에서 `/news-brief`를 실행한다.

### 수집 구간

기본은 직전 24시간이다. 월요일이나 연휴 다음 날에는 직전 미국 장 마감(평일 16:00
America/New_York) 이후로 자동으로 넓어진다. 두 값 중 이른 쪽을 쓰므로 연휴가 길수록
구간이 자동으로 늘어난다.

```bash
python3 engine/collect.py --since 2026-09-08T07:00+09:00   # 직접 지정
python3 engine/collect.py --max-per-category 50            # 후보 목록 분량 조절
```

### 검증

```bash
python3 engine/check_feeds.py                        # 50개 피드가 살아있는지
python3 engine/validate_report.py reports/2026-09-09.md
python3 engine/check_links.py reports/2026-09-09.md
```

`validate_report.py`는 고정 출력 구조(6-1~6-8), 10개 카테고리 출력, Event ID `#1`~`#20`
연속성과 20건 상한, `시장 환경` 값이 우호/혼조/부담/판단 불가 중 하나인지, 매수·매도·
목표가격·포지션 비중 같은 행동 경계 위반, Coverage Audit 필수 항목을 검사한다.

`check_links.py`는 404를 **실패**로, 403·유료벽·타임아웃을 **확인 불가**로 구분한다.
확인 불가는 Coverage Audit에 기록하고, 실패한 링크가 있는 브리핑은 내보내지 않는다.

## 자동 실행

매일 07:00 KST에 Routine이 이 리포지토리에서 세션을 띄워 전체 파이프라인을 돌린다.
claude.ai 구독 한도로 실행되므로 API 요금이 발생하지 않는다. Routine 목록은 claude.ai
설정에서 확인·수정할 수 있다.

전달 경로는 `engine/delivery.json`에서 켜고 끈다.

## 소스 관리

`engine/sources.json`이 카테고리별 피드 목록이다. `tier`는 v4.0의 출처 우선순위를
따른다 (1=공식 원자료, 2=전문 통신·금융 매체, 3=주요 해설 매체, 4=기타 현지 매체).

**피드를 추가하거나 고치면 반드시 `python3 engine/check_feeds.py`를 돌린다.** 죽은
피드를 목록에 남겨두면 Coverage Audit의 '검색 완료' 표시가 거짓이 된다.

### 피드로 수집할 수 없는 기관

`sources.json`의 `candidate_feeds`에 15개가 남아 있고, `blocked_reason`이 재검증
방법을 가른다.

- `waf`(8) · `egress_policy`(1) — BLS·한국은행·한국경제·NY Fed·IMF·CNBC·IEA 등이
  데이터센터 IP를 차단한다(403 또는 Cloudflare 챌린지). **다른 네트워크에서는 풀릴 수
  있다.** `python3 engine/check_feeds.py --candidates`로 재확인한다.
- `none`(6) — BIS·Japan MOF·기획재정부·PBOC·NHK·Korea Herald는 RSS 미제공 또는 정지로
  확정됐다. 재시도해도 소용없으므로 대안 소스가 필요하다.

**한국과 중국 카테고리에는 tier 1 공식 원자료 피드가 없다.** 이 두 카테고리의 공식
발표는 LLM 단계의 웹 검색 보강에 의존하며, 그 사실은 Coverage Audit에 기록된다.
자세한 조사 결과와 재검증 절차는 [docs/FOLLOW-UP.md](docs/FOLLOW-UP.md)에 있다.

## 비용

Routine 실행은 claude.ai 구독 한도를 쓴다. 나중에 Anthropic API + GitHub Actions로
바꾸면 종량제 요금이 발생하며, 하루 1회 기준 추정 비용은 Sonnet 5 약 $11~24/월,
Opus 5 약 $27~60/월이다(수집량에 따라 달라진다).
