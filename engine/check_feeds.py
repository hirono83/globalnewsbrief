#!/usr/bin/env python3
"""sources.json의 모든 피드가 살아있는지 전수 검사한다.

죽은 피드를 목록에 남겨두면 Coverage Audit의 '검색 완료' 표시가 거짓말이 되므로,
소스를 추가·수정할 때마다 이 스크립트를 돌려 확인한다.

    python3 engine/check_feeds.py            # 전체 검사
    python3 engine/check_feeds.py --json     # 기계가 읽을 결과
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
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    feeds = json.loads(SOURCES.read_text(encoding="utf-8"))["feeds"]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(check, feeds))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if all(r["ok"] for r in results) else 1

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    for r in sorted(ok, key=lambda r: (r["category"], r["name"])):
        print(f"  OK   [{r['category']}] {r['name']}  ({r['articles']}건, 최신 {r['latest']})")
    for r in sorted(bad, key=lambda r: (r["category"], r["name"])):
        print(f"  FAIL [{r['category']}] {r['name']}  {r['url']}\n         → {r['error']}")
    print(f"\n정상 {len(ok)}/{len(results)} · 실패 {len(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
