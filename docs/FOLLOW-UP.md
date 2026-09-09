# 후속 작업: 다른 네트워크에서 재검증

이 엔진은 Claude Code 클라우드 컨테이너에서 만들어졌다. 그 환경의 데이터센터 IP가
여러 기관 사이트에서 차단당하는 탓에, **아직 확보하지 못한 공식 원자료 피드가 있고
전달 경로 일부가 검증되지 않았다.** 가정용 회선 등 다른 네트워크에서 아래를 확인한다.

## 1. 차단된 원자료 피드 재확보

```bash
python3 engine/check_feeds.py --candidates
```

`engine/sources.json` 의 `candidate_feeds` 18개를 재검증한다. 통과한 항목은
`복구!` 로 표시되고, 스크립트가 어떤 항목을 `feeds` 배열로 옮기면 되는지 알려준다.
옮긴 뒤에는 `python3 engine/check_feeds.py` 가 전부 통과하는지 확인한다.

`blocked_reason` 이 재검증 방법을 가른다.

| 사유 | 건수 | 다른 네트워크에서 해결되는가 | 할 일 |
| --- | --- | --- | --- |
| `waf` | 7 | **가능성 높음** | 그대로 재시도 |
| `egress_policy` | 1 | 가능성 높음 | 그대로 재시도 (AP) |
| `url_unknown` | 9 | **아니오** | 기관 사이트에서 실제 피드 주소를 찾아야 한다 |
| `none` | 1 | 아니오 | RSS 자체가 없다. 대안 소스나 스크래핑을 검토 |

우선순위가 높은 것부터.

1. **BLS** (`waf`) — 고용·CPI 발표의 1차 출처. 지금은 미국 물가·고용 뉴스를 2차
   매체로만 받는다. 가치가 가장 크다.
2. **한국은행** (`waf`, HTTP 500) — 한국 카테고리에 tier 1 원자료가 하나도 없는
   주된 이유. 500이라 IP 문제가 아닐 수도 있으니 실제 피드 주소부터 다시 확인한다.
3. **한국경제** (`waf`) — Cloudflare 챌린지를 200으로 반환한다. 로컬 브라우저
   환경에서 가장 쉽게 뚫릴 축.
4. **NY Fed** (`waf`) — 공개시장운영·레포 1차 출처.
5. **기획재정부 · Caixin · Kyodo · NHK · Japan MOF · Bundesbank · BIS · US Treasury**
   (`url_unknown`) — 각 기관 사이트 하단이나 뉴스룸 페이지에서 RSS 링크를 직접 찾는다.
   추측한 주소를 넣지 말고 반드시 `check_feeds.py` 로 확인한 것만 커밋한다.

`url_unknown` 항목을 고칠 때는 `candidate_feeds` 의 `detail` 에 이미 시도해서 실패한
주소들이 적혀 있으니 같은 걸 다시 시도하지 않는다.

## 2. Routine 커넥터 연결 (미완료)

Routine `글로벌 뉴스 브리핑 (매일 07:00 KST)` 은 등록됐지만 **커넥터가 붙어 있지
않다.** 조직 정책상 `create_trigger` MCP 도구로는 커넥터를 전달할 수 없다
(`the connectors parameter is not available for this organization`).

지금 상태로는 커밋만 되고 이메일·Notion 전달이 되지 않는다.

- claude.ai의 Routines 설정에서 이 Routine에 **Gmail**과 **Notion**을 붙인다.
- 같은 계정의 `매일 아침 주식 브리핑` Routine에는 이미 Gmail·Notion·PlayMCP가
  붙어 있으므로, 그 설정을 참고하면 된다.
- 붙인 뒤 Routine을 수동으로 한 번 실행해 메일과 Notion 페이지가 실제로 도착하는지
  확인한다. 스킬은 커넥터가 없으면 조용히 넘어가지 않고 `이메일 발송 불가 — 커넥터
  없음` 처럼 사유를 보고하게 되어 있다.

## 3. 종단 테스트

```bash
python3 engine/collect.py
python3 engine/market_data.py
# Claude Code에서 /news-brief 실행
python3 engine/validate_report.py reports/$(TZ=Asia/Seoul date +%F).md
python3 engine/check_links.py reports/$(TZ=Asia/Seoul date +%F).md
```

확인할 것.

- [ ] 10개 카테고리가 모두 출력되고, 비어 있는 카테고리는 `검색 완료 — 금일 중요
      신규 사건 없음` 으로 표시되는가
- [ ] Event ID가 `#1` 부터 연속이고 20을 넘지 않는가
- [ ] 핵심 사건 5건에 `[사실]` `[해석]` `[불확실]` `시각:` `출처:` 가 모두 있는가
- [ ] `check_links.py` 에 `실패` 가 0건인가 (404 링크는 모델이 지어낸 URL이라는 뜻)
- [ ] Coverage Audit의 수치가 `out/candidates.json` 의 `meta` 와 어긋나지 않는가
- [ ] 웹 검색으로 보강한 카테고리가 Coverage Audit에 기록됐는가
- [ ] 커밋 · 이메일 · Notion 세 경로가 모두 성공했는가
- [ ] 월요일에 실행했을 때 수집 구간이 자동으로 넓어지는가
      (`python3 engine/collect.py` 첫 줄의 구간 표시로 확인)

## 4. 알려진 한계

- **한국·중국 카테고리에 tier 1 공식 원자료 피드가 없다.** 한국은행·기획재정부·
  PBOC·중국 국가통계국은 RSS를 제공하지 않거나 차단한다. 두 카테고리의 공식 발표는
  LLM 단계의 웹 검색 보강에 의존하며, 이는 파이프라인이 보장하는 재현성 밖에 있다.
- **언어가 다른 같은 사건은 파이썬 단계에서 병합되지 않는다.** 제목 토큰 유사도로만
  묶기 때문이다. 교차 언어 병합은 LLM 단계(Step 2)가 맡는다.
- **Global Times 피드가 오래된 항목을 반환할 때가 있다.** 수집 구간 필터에 걸러지므로
  해는 없지만, 중국 카테고리의 실질 커버리지가 SCMP 두 개에 쏠린다.
- **가격 데이터는 미 국채 수익률뿐이다.** 주가지수·환율·원자재·암호화폐 가격은
  확인되지 않으므로 브리핑에서 해당 자산의 가격 반응을 만들지 않고 `가격 확인 필요`로
  쓴다. 무료·무인증 공식 소스를 더 찾으면 `engine/market_data.py` 에 추가한다.
