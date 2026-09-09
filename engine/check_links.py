#!/usr/bin/env python3
"""브리핑에 실린 링크가 실제로 존재하는지 확인한다.

LLM이 만든 그럴듯한 URL이 그대로 나가는 것을 막는 마지막 관문이다. 404는 링크가
없는 것이므로 실패로 처리하고, 403·유료벽·타임아웃은 '확인 불가'로 분류해 v4.0의
Coverage Audit(확인 불가 또는 유료벽 뉴스)에 기록할 수 있게 한다.

    python3 engine/check_links.py reports/2026-09-09.md
    python3 engine/check_links.py reports/2026-09-09.md --json
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.feedlib import USER_AGENT

MD_LINK_RE = re.compile(r"\[([^\]]{1,200})\]\((https?://[^\s)]+)\)")

# 본문 확인이 불가능하다는 뜻일 뿐, 링크가 없다는 뜻은 아닌 상태 코드.
UNVERIFIABLE_CODES = {401, 403, 405, 406, 429, 451, 500, 502, 503, 504}


def probe(url: str, timeout: int = 20) -> dict:
    result = {"url": url, "status": None, "verdict": "확인 불가", "detail": None}
    context = ssl.create_default_context()
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(
            url, method=method, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                result["status"] = response.status
                result["verdict"] = "정상"
                return result
        except urllib.error.HTTPError as exc:
            result["status"] = exc.code
            if exc.code in (404, 410):
                result["verdict"] = "실패"
                result["detail"] = f"HTTP {exc.code} — 링크가 존재하지 않음"
                return result
            if exc.code in UNVERIFIABLE_CODES:
                result["detail"] = f"HTTP {exc.code} — 접근 차단 또는 유료벽으로 본문 확인 불가"
                if method == "GET":
                    return result
                continue
            result["detail"] = f"HTTP {exc.code}"
            if method == "GET":
                return result
        except Exception as exc:
            result["detail"] = f"{type(exc).__name__}: {exc}"[:120]
            if method == "GET":
                return result
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="브리핑 링크 실재 검증")
    parser.add_argument("report", help="검사할 마크다운 파일")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    text = Path(args.report).read_text(encoding="utf-8")
    urls: list[str] = []
    for _, url in MD_LINK_RE.findall(text):
        url = url.rstrip(".,;")
        if url not in urls:
            urls.append(url)

    if not urls:
        print("링크가 하나도 없습니다. v4.0은 모든 채택 뉴스에 직접 링크를 요구합니다.", file=sys.stderr)
        return 1

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(probe, urls))

    broken = [r for r in results if r["verdict"] == "실패"]
    unverified = [r for r in results if r["verdict"] == "확인 불가"]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if r["verdict"] == "정상":
                print(f"  정상      {r['url']}")
        for r in unverified:
            print(f"  확인 불가 {r['url']}\n            → {r['detail']}")
        for r in broken:
            print(f"  실패      {r['url']}\n            → {r['detail']}")
        print(
            f"\n링크 {len(results)}개 · 정상 {len(results) - len(broken) - len(unverified)} · "
            f"확인 불가 {len(unverified)} · 실패 {len(broken)}"
        )
        if unverified:
            print("확인 불가 링크는 Coverage Audit의 '확인 불가 또는 유료벽 뉴스'에 적는다.")
        if broken:
            print("실패한 링크는 존재하지 않는다. 해당 뉴스를 빼거나 올바른 링크로 고쳐야 한다.")

    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
