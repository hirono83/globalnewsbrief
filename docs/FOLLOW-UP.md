# 후속 작업: 다른 네트워크에서 재검증

이 엔진은 Claude Code 클라우드 컨테이너에서 만들어졌다. 그 환경의 데이터센터 IP가
여러 기관 사이트에서 차단당하는 탓에, **아직 확보하지 못한 공식 원자료 피드가 있고
전달 경로 일부가 검증되지 않았다.** 가정용 회선 등 다른 네트워크에서 아래를 확인한다.

## 1. 차단된 원자료 피드 재확보

```bash
python3 engine/check_feeds.py --candidates
```

`engine/sources.json` 의 `candidate_feeds` 를 재검증한다. 통과한 항목은 `복구!` 로
표시되고, 스크립트가 어떤 항목을 `feeds` 배열로 옮기면 되는지 알려준다.

### 2026-09-09 조사로 해소된 것 (url_unknown 9건)

주소가 틀렸던 항목은 IP와 무관하므로 클라우드 환경에서 모두 조사를 마쳤다. 6건을
활성 목록으로 옮겼고, 3건은 RSS 미제공으로 확정했다.

| 피드 | 결과 |
| --- | --- |
| Bundesbank — 연방채 입찰결과 | 복구. 확장자가 `.xml` 이 아니라 `.rss` 였다. 채권·금리 tier 1 |
| Bundesbank — ECB 유동성공급 배분 | 복구. 유럽 tier 1 |
| Bundesbank — 조사·정책 | 복구. 유럽 tier 1 |
| Kyodo News | 복구. 실제 주소는 `/list/feed/rss4kyodonews-fzone` |
| 아시아경제 경제 | 신규 확보. 한국 카테고리 보강 |
| US Treasury | 복구(`/rss.xml`). 다만 갱신이 드물다 |
| BIS | RSS 미제공 확정. 홈페이지에 링크 없고 경로 5종 모두 404 |
| Japan MOF · 기획재정부 | RSS 미제공 확정. 홈페이지에 링크 없음 |
| NHK · Korea Herald | 피드 정지 또는 빈 피드로 확정 |
| Caixin Global | 실제 주소는 찾았으나 게이트웨이가 Accept 헤더 무관 406 |

이 작업으로 활성 피드가 50개에서 56개가 됐고, 채권·금리 카테고리의 공식 원자료가
0건에서 2건, 유럽이 6건에서 10건으로 늘었다.

### 2026-09-10 재검증 결과 (클라우드 컨테이너에서 실행)

이 재검증도 Claude Code 클라우드 컨테이너에서 돌렸다. **차단 해소는 IP가 바뀌어야
하므로 15건 중 복구된 것은 없다.** 다만 IP와 무관하게 판별 가능한 두 가지가 정리됐다.

- **한국은행을 `waf` 에서 `none` 으로 확정했다.** 같은 IP에서 `main.do` 와 **같은
  게시판의 `list.do` 가 HTTP 200** 으로 정상 응답하는데 `rss.do` 만 파라미터와 무관하게
  HTTP 500(618바이트 오류 페이지)을 낸다. IP 차단이면 게시판 페이지도 함께 막혀야
  하므로, 이건 RSS 엔드포인트 자체의 서버측 장애다. 다른 회선에서 재시도할 필요가 없다.
  대안으로 검토한 보도자료 목록 페이지 스크래핑도 **목록이 자바스크립트로만 렌더링돼
  HTML에 `nttId` 가 하나도 없어** 표준 라이브러리로는 불가능하다. 남은 길은 다른 경제지
  피드이며, 이미 활성 목록의 연합뉴스·아시아경제·매일경제·동아일보가 한은 발표를
  당일 받아온다(2026-09-10 통화신용정책보고서가 그 예다).
- **한국경제는 오히려 더 조여졌다.** 이전에는 Cloudflare 챌린지를 HTTP 200으로
  반환했는데, 지금은 `HTTP 403` + `cf-mitigated: challenge` 다. 브라우저 User-Agent로도
  동일하다.

나머지는 순수한 IP 차단으로 재확인됐다. 브라우저 User-Agent와 `Accept` 헤더를 붙여도
응답이 같아 헤더 문제가 아니다.

| 피드 | 응답 | 차단 주체 |
| --- | --- | --- |
| BLS · IMF · CNBC | HTTP 403 | Akamai (`server: AkamaiGHost`) |
| NY Fed | HTTP 403 | Akamai (`_abck` 쿠키 발급) |
| IEA · 한국경제 | HTTP 403 | Cloudflare (`cf-mitigated: challenge`) |
| AP | CONNECT 502 | 이 세션의 egress 프록시 (`egress_policy`) |

**따라서 1번 작업은 가정용 회선에서 `python3 engine/check_feeds.py --candidates` 를
한 번 돌리는 것만 남았다.** 통과한 항목은 `복구!` 로 표시된다.

### 다른 네트워크에서만 확인 가능한 것 (waf 7건 · egress_policy 1건)

특정 IP를 차단당한 것이라 **가정용 회선에서 다시 시도해야 한다.** 우선순위 순.

1. **BLS** — 고용·CPI 발표의 1차 출처. 지금은 미국 물가·고용 뉴스를 2차 매체로만 받는다.
   가치가 가장 크다.
2. ~~한국은행~~ — 위에서 `none` 으로 확정됐다. 재시도 불필요.
3. **한국경제** — Cloudflare 챌린지. 2026-09-10 기준 HTTP 403 + `cf-mitigated: challenge`로
   이전보다 강해졌다. 그래도 가정용 회선에서는 통과할 수 있다.
4. **NY Fed** — 공개시장운영·레포 1차 출처.
5. IMF · CNBC · IEA · Caixin · AP — 나머지.

`none` 으로 표시된 7건(BIS · Japan MOF · 기획재정부 · PBOC · NHK · Korea Herald ·
한국은행)은 다른 네트워크에서도 풀리지 않는다. 재시도하지 말고 대안 소스를 찾아야 한다.

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

- [x] 10개 카테고리가 모두 출력되는가 — 2026-09-10 확인. 다만 빈 카테고리가
      하나도 없어 `검색 완료 — 금일 중요 신규 사건 없음` 경로는 아직 미검증
- [x] Event ID가 `#1` 부터 연속이고 20을 넘지 않는가 — #1~#20, 검증기 통과
- [x] 핵심 사건 5건에 `[사실]` `[해석]` `[불확실]` `시각:` `출처:` 가 모두 있는가 — 통과
- [x] `check_links.py` 에 `실패` 가 0건인가 — 링크 36개 · 정상 32 · 확인 불가 4 · 실패 0
- [x] Coverage Audit의 수치가 `out/candidates.json` 의 `meta` 와 어긋나지 않는가 —
      2,021 / 932 / 868로 일치
- [x] 웹 검색으로 보강한 카테고리가 Coverage Audit에 기록됐는가 — 한국·미국·유럽 3건
- [x] 커밋 · 이메일 · Notion 세 경로가 모두 성공했는가 — 2026-09-10 세 경로 모두 확인.
      다만 같은 날 두 세션이 각각 브리핑을 만들어 `reports/2026-09-10.md` 가 add/add로
      충돌했고, 이메일도 두 번 나갔다. **동시 실행 방어가 없다.** 재실행 전에 이미
      같은 날짜의 커밋·메일·Notion 페이지가 있는지 확인하는 절차가 필요하다.
- [x] 월요일에 실행했을 때 수집 구간이 자동으로 넓어지는가 — `default_window_start()` 를
      요일별로 호출해 확인. 월 07:00 KST는 50시간(금 미국 장 마감 이후), 화~토는 24시간

## 4. 알려진 한계

- **한국·중국 카테고리에 tier 1 공식 원자료 피드가 없다.** 기획재정부·Japan MOF·
  PBOC·중국 국가통계국은 RSS 미제공으로 확정됐고, 한국은행도 RSS 엔드포인트
  서버측 장애(HTTP 500)로 2026-09-10 확정됐다. 두 카테고리의 공식 발표는
  LLM 단계의 웹 검색 보강에 의존하며, 이는 파이프라인이 보장하는 재현성 밖에 있다.
- **언어가 다른 같은 사건은 파이썬 단계에서 병합되지 않는다.** 제목 토큰 유사도로만
  묶기 때문이다. 교차 언어 병합은 LLM 단계(Step 2)가 맡는다.
- **Global Times 피드가 오래된 항목을 반환할 때가 있다.** 수집 구간 필터에 걸러지므로
  해는 없지만, 중국 카테고리의 실질 커버리지가 SCMP 두 개에 쏠린다.
- **가격 데이터는 미 국채 수익률뿐이다.** 주가지수·환율·원자재·암호화폐 가격은
  확인되지 않으므로 브리핑에서 해당 자산의 가격 반응을 만들지 않고 `가격 확인 필요`로
  쓴다. 무료·무인증 공식 소스를 더 찾으면 `engine/market_data.py` 에 추가한다.
