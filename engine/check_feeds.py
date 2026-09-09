#!/usr/bin/env python3
"""sources.json의 모든 피드가 살아있는지 전수 검사한다.

죽은 피드를 목록에 남겨두면 Coverage Audit의 '검색 완료' 표시가 거짓말이 되므로,
소스를 추가·수정할 때마다 이 스크립트를 돌려 확인한다.

    python3 engine/check_feeds.py              # 활성 피드 전수 검사
    python3 engine/check_feeds.py --candidates # 차단됐던 후보 피드 재검증
    python3 engine/check_feeds.py --all --json # 둘 다, 기계가 읽을 결과

--candidates 는 다른 네트워크에서 돌릴 때 의미가 있다. blocked_reason 이 waf 나
egress_policy 인 항목은 특정 IP를 차단당한 것이므로 가정용 회선 등에서는 통과할 수
있다. url_unknown 은 주소 자체가 틀린 것이라 IP를 바꿔도 통과하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import feedlib

SOURCES = Path(__file__).resolve().parent / "sources.json"


def check(feed: dict) -> dict:
    result = {
        "category": feed["category"],
        "name": feed["name"],
        "url": feed["url"],
        "tier": feed["tier"],
        "candidate": "blocked_reason" in feed,
        "blocked_reason": feed.get("blocked_reason"),
        "ok": False,
        "articles": 0,
        "latest": None,
        "error": None,
    }
    try:
        payload = feedlib.fetch(feed["url"], timeout=25, retries=1)
        articles = feedlib.parse_feed(payload, feed["name"], feed["tier"], feed["category"], feed["url"])
    except feedlib.FeedError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # 피드 하나가 전체 검사를 죽이지 않게 한다
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["articles"] = len(articles)
    dated = [a.published for a in articles if a.published]
    if dated:
        result["latest"] = max(dated).astimezone(feedlib.KST).strftime("%Y-%m-%d %H:%M KST")
    if not articles:
        result["error"] = "항목 0건"
        return result
    result["ok"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    parser.add_argument(
        "--candidates",
        action="store_true",
        help="활성 피드 대신 차단됐던 후보 피드를 재검증한다",
    )
    parser.add_argument("--all", action="store_true", help="활성 피드와 후보를 모두 검사")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if args.all:
        feeds = sources["feeds"] + sources.get("candidate_feeds", [])
    elif args.candidates:
        feeds = sources.get("candidate_feeds", [])
        if not feeds:
            print("후보 피드가 없습니다. 모두 활성 목록으로 옮겨졌습니다.")
            return 0
    else:
        feeds = sources["feeds"]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(check, feeds))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if all(r["ok"] for r in results) else 1

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    for r in sorted(ok, key=lambda r: (r["category"], r["name"])):
        mark = "복구!" if r["candidate"] else "OK   "
        print(f"  {mark} [{r['category']}] {r['name']}  ({r['articles']}건, 최신 {r['latest']})")
    for r in sorted(bad, key=lambda r: (r["category"], r["name"])):
        reason = f" ({r['blocked_reason']})" if r["blocked_reason"] else ""
        print(f"  FAIL [{r['category']}] {r['name']}{reason}  {r['url']}\n         → {r['error']}")

    recovered = [r for r in ok if r["candidate"]]
    active_bad = [r for r in bad if not r["candidate"]]
    print(f"\n정상 {len(ok)}/{len(results)} · 실패 {len(bad)}")
    if recovered:
        print(
            f"\n후보 피드 {len(recovered)}개가 이 네트워크에서는 살아납니다. "
            f"sources.json의 candidate_feeds에서 feeds로 옮기세요:"
        )
        for r in recovered:
            print(f"  - [{r['category']}] {r['name']}")

    # 활성 목록에 있어야 할 피드가 죽은 것만 실패로 본다. 후보가 여전히 막힌 것은
    # 이미 알고 있는 사실이므로 종료 코드를 더럽히지 않는다.
    return 1 if active_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
