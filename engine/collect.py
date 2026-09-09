#!/usr/bin/env python3
"""수집 구간 안의 기사를 모아 정규화하고 1차 중복제거를 수행한다.

이 스크립트는 판단을 하지 않는다. 중요도 평가, 사건 단위 병합, 브리핑 작성은
LLM 단계(.claude/skills/news-brief)의 몫이고, 여기서는 검증 가능한 사실만 만든다.

    python3 engine/collect.py                       # 기본 수집 구간
    python3 engine/collect.py --since 2026-09-08T07:00+09:00
    python3 engine/collect.py --out out --max-per-category 60
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import feedlib, scoring
from engine.feedlib import KST, Article

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "engine" / "sources.json"
NY = ZoneInfo("America/New_York")

CATEGORY_ORDER = [
    "글로벌", "미국", "유럽", "중국", "일본", "한국",
    "암호화폐", "채권·금리", "원자재", "IT·테크",
]

# 제목 유사도 계산에서 뺄 기능어. 언어별로 흔한 것만 담는다.
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "at", "by",
    "with", "from", "as", "is", "are", "was", "were", "be", "been", "will",
    "says", "say", "said", "after", "over", "amid", "new", "up", "down",
    "its", "his", "her", "their", "that", "this", "it", "but", "not",
    "속보", "단독", "종합", "영상", "포토", "그래픽", "사진", "인터뷰",
    "및", "등", "관련", "위해", "대한", "따른", "통해",
}

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_BRACKET_RE = re.compile(r"[\[\(【〔<][^\]\)】〕>]{0,20}[\]\)】〕>]")


@dataclass
class Cluster:
    """같은 사건을 다룬 것으로 보이는 기사 묶음(1차 중복제거 결과)."""

    articles: list[Article]
    score: float = 0.0
    topics: list[str] = field(default_factory=list)

    @property
    def lead(self) -> Article:
        """대표 기사: 출처 우선순위가 높고, 같으면 먼저 보도한 쪽."""
        return min(
            self.articles,
            key=lambda a: (
                a.source_tier,
                a.published or datetime.max.replace(tzinfo=feedlib.UTC),
            ),
        )

    @property
    def sources(self) -> list[str]:
        seen: list[str] = []
        for a in self.articles:
            if a.source_name not in seen:
                seen.append(a.source_name)
        return seen

    @property
    def has_primary_source(self) -> bool:
        return any(a.source_tier == 1 for a in self.articles)


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title).lower()
    text = _BRACKET_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_tokens(title: str) -> frozenset[str]:
    """유사도 비교용 토큰. 한글은 2자 이상, 그 외는 3자 이상만 남긴다."""
    tokens = set()
    for word in normalize_title(title).split():
        if word in STOPWORDS or word.isdigit():
            continue
        is_cjk = any("가" <= ch <= "힣" or "一" <= ch <= "鿿" for ch in word)
        if len(word) >= (2 if is_cjk else 3):
            tokens.add(word)
    return frozenset(tokens)


def similar(a: frozenset[str], b: frozenset[str]) -> bool:
    """두 제목이 같은 사건을 가리키는지에 대한 보수적 판정.

    느슨하게 잡으면 서로 다른 사건이 하나로 뭉쳐 뉴스가 사라지므로, 놓치는 쪽보다
    합치지 않는 쪽으로 기운 임계값을 쓴다. 언어가 다른 같은 사건은 여기서 잡히지
    않으며, 그 병합은 LLM 단계가 맡는다.
    """
    if not a or not b:
        return False
    overlap = len(a & b)
    if overlap < 2:
        return False
    # 짧은 제목 기준 포함율과 자카드를 함께 본다.
    containment = overlap / min(len(a), len(b))
    jaccard = overlap / len(a | b)
    return containment >= 0.75 or jaccard >= 0.55


def default_window_start(now: datetime) -> tuple[datetime, str]:
    """기본 수집 구간의 시작점.

    v4.0 실행 기준: 기본은 직전 24시간. 다만 월요일이나 연휴 다음 날에는 마지막
    주요 시장 마감 이후를 쓴다. 직전 미국 장 마감(평일 16:00 America/New_York)과
    24시간 전 중 이른 쪽을 고르면 두 규칙이 동시에 만족된다. 주말·휴일이 길수록
    구간이 자동으로 늘어난다.
    """
    twenty_four = now - timedelta(hours=24)

    probe = now.astimezone(NY)
    for _ in range(10):
        close = probe.replace(hour=16, minute=0, second=0, microsecond=0)
        if close < now.astimezone(NY) and close.weekday() < 5:
            break
        probe -= timedelta(days=1)
    else:  # pragma: no cover - 10일 안에 평일이 없을 수는 없다
        return twenty_four, "직전 24시간"

    last_close = close.astimezone(now.tzinfo)
    if last_close < twenty_four:
        return last_close, f"마지막 주요 시장 마감(미국 {close:%Y-%m-%d %H:%M %Z}) 이후"
    return twenty_four, "직전 24시간"


def fetch_all(feeds: list[dict], workers: int) -> tuple[list[Article], list[dict]]:
    def one(feed: dict) -> tuple[list[Article], dict]:
        status = {
            "category": feed["category"],
            "name": feed["name"],
            "url": feed["url"],
            "tier": feed["tier"],
            "ok": False,
            "fetched": 0,
            "error": None,
        }
        try:
            payload = feedlib.fetch(feed["url"])
            articles = feedlib.parse_feed(
                payload, feed["name"], feed["tier"], feed["category"], feed["url"]
            )
        except Exception as exc:
            status["error"] = f"{type(exc).__name__}: {exc}"[:200]
            return [], status
        status["ok"] = True
        status["fetched"] = len(articles)
        return articles, status

    articles: list[Article] = []
    statuses: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for got, status in pool.map(one, feeds):
            articles.extend(got)
            statuses.append(status)
    return articles, statuses


def dedupe(articles: list[Article]) -> list[Cluster]:
    """URL 정규화와 제목 유사도로 기사 목록을 사건 후보 묶음으로 줄인다."""
    # 1단계: 정규화 URL이 같으면 같은 기사.
    by_url: dict[str, list[Article]] = {}
    for article in articles:
        by_url.setdefault(feedlib.canonical_url(article.link), []).append(article)
    groups = list(by_url.values())

    # 2단계: 제목 유사도로 묶음끼리 합친다. 전수 비교를 피하려고 토큰 역색인으로
    # 후보를 좁힌 뒤 union-find로 연결한다.
    tokens = [title_tokens(group[0].title) for group in groups]
    parent = list(range(len(groups)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    index: dict[str, list[int]] = {}
    for i, token_set in enumerate(tokens):
        candidates: set[int] = set()
        for token in token_set:
            candidates.update(index.get(token, ()))
        for j in candidates:
            if find(i) != find(j) and similar(token_set, tokens[j]):
                union(i, j)
        for token in token_set:
            index.setdefault(token, []).append(i)

    merged: dict[int, list[Article]] = {}
    for i, group in enumerate(groups):
        merged.setdefault(find(i), []).extend(group)

    return [Cluster(articles=group) for group in merged.values()]


def render_markdown(
    by_category: dict[str, list[Cluster]],
    meta: dict,
    coverage: list[dict],
    max_per_category: int,
) -> str:
    lines = [
        "# 브리핑 후보 (기계 수집 결과)",
        "",
        f"- 수집 구간: {meta['window_start_kst']} ~ {meta['window_end_kst']} ({meta['window_label']})",
        f"- 피드: 정상 {meta['feeds_ok']}/{meta['feeds_total']}",
        f"- 구간 내 기사: {meta['articles_in_window']}건 → 1차 중복제거 후 후보 {meta['clusters']}건",
        "",
        "각 줄은 `[번호] 제목 | 게시시각 | 출처(티어) | 주제 | 링크` 형식이다. `↳`는",
        "같은 사건을 보도한 다른 매체이며, 독립 근거가 아니라 중복 보도 수를 뜻한다.",
        "게시시각은 피드가 준 값이므로 사건 발생시각과 다를 수 있다.",
        "",
        "번호는 기계적 사전정렬 순서일 뿐 중요도 판정이 아니다. 중요도는 시장 영향",
        "범위·예상 밖 정도·지속 가능성·실제 가격 반응으로 다시 판단해야 한다.",
        "",
    ]

    for category in CATEGORY_ORDER:
        clusters = by_category.get(category, [])
        status = next((c for c in coverage if c["category"] == category), None)
        failed = status["feeds_failed"] if status else 0
        header = f"## [{category}] 후보 {len(clusters)}건"
        if failed:
            header += f" · 피드 실패 {failed}개 — 누락 가능성 있음"
        lines.append(header)
        lines.append("")
        if not clusters:
            lines.append("- 구간 내 신규 기사 없음")
            lines.append("")
            continue
        for cluster in clusters[:max_per_category]:
            lead = cluster.lead
            when = (
                lead.published.astimezone(KST).strftime("%m-%d %H:%M KST")
                if lead.published
                else "시각 확인 불가"
            )
            topics = f" | 주제: {', '.join(cluster.topics)}" if cluster.topics else ""
            lines.append(
                f"- [{lead.cluster_id}] {lead.title} | {when} | "
                f"{lead.source_name}(T{lead.source_tier}){topics} | {lead.link}"
            )
            if lead.summary:
                lines.append(f"    · {lead.summary[:180]}")
            others = [s for s in cluster.sources if s != lead.source_name]
            if others:
                lines.append(f"    ↳ 중복 보도 {len(others)}개 매체: {', '.join(others[:6])}")
        if len(clusters) > max_per_category:
            lines.append(
                f"- (중요도 하위 {len(clusters) - max_per_category}건은 지면상 생략, "
                f"candidates.json에는 모두 있음)"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="뉴스 브리핑 후보 수집기")
    parser.add_argument("--since", help="수집 구간 시작(ISO8601). 미지정 시 자동 계산")
    parser.add_argument("--now", help="기준시각 override(ISO8601). 테스트용")
    parser.add_argument("--out", default="out", help="결과 디렉터리 (기본: out)")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument(
        "--max-per-category", type=int, default=35, help="markdown에 실을 카테고리별 최대 후보 수"
    )
    args = parser.parse_args()

    now = feedlib.parse_date(args.now) if args.now else datetime.now(KST)
    if now is None:
        print("--now 값을 해석할 수 없습니다.", file=sys.stderr)
        return 2
    now = now.astimezone(KST)

    if args.since:
        window_start = feedlib.parse_date(args.since)
        if window_start is None:
            print("--since 값을 해석할 수 없습니다.", file=sys.stderr)
            return 2
        window_start, window_label = window_start.astimezone(KST), "사용자 지정 구간"
    else:
        window_start, window_label = default_window_start(now)

    feeds = json.loads(SOURCES.read_text(encoding="utf-8"))["feeds"]
    articles, statuses = fetch_all(feeds, args.workers)

    # 게시시각이 없는 기사는 구간 판정이 불가능하다. 버리지 않고 표시만 남긴다.
    in_window = [
        a for a in articles if a.published is None or window_start <= a.published <= now
    ]
    undated = sum(1 for a in in_window if a.published is None)

    clusters = dedupe(in_window)
    for cluster in clusters:
        lead = cluster.lead
        cluster.score, cluster.topics = scoring.prescore(
            tier=lead.source_tier,
            outlet_count=len(cluster.sources),
            title=lead.title,
            summary=lead.summary,
            published=lead.published,
            now=now,
        )
    # 번호는 사전정렬 순서대로 붙인다. LLM 단계가 위에서부터 읽으면 된다.
    clusters.sort(key=lambda c: -c.score)
    for number, cluster in enumerate(clusters, start=1):
        for article in cluster.articles:
            article.cluster_id = number
        cluster.lead.duplicates = [s for s in cluster.sources if s != cluster.lead.source_name]

    by_category: dict[str, list[Cluster]] = {}
    for cluster in clusters:
        by_category.setdefault(cluster.lead.category, []).append(cluster)

    coverage = []
    for category in CATEGORY_ORDER:
        cat_feeds = [s for s in statuses if s["category"] == category]
        cat_clusters = by_category.get(category, [])
        coverage.append(
            {
                "category": category,
                "feeds_total": len(cat_feeds),
                "feeds_ok": sum(1 for s in cat_feeds if s["ok"]),
                "feeds_failed": sum(1 for s in cat_feeds if not s["ok"]),
                "failed_feeds": [s["name"] for s in cat_feeds if not s["ok"]],
                "articles_in_window": sum(
                    1 for a in in_window if a.category == category
                ),
                "candidate_events": len(cat_clusters),
                "with_primary_source": sum(1 for c in cat_clusters if c.has_primary_source),
                "searched": any(s["ok"] for s in cat_feeds),
            }
        )

    meta = {
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M KST"),
        "window_start_kst": window_start.strftime("%Y-%m-%d %H:%M KST"),
        "window_end_kst": now.strftime("%Y-%m-%d %H:%M KST"),
        "window_label": window_label,
        "feeds_total": len(statuses),
        "feeds_ok": sum(1 for s in statuses if s["ok"]),
        "feeds_failed": [s for s in statuses if not s["ok"]],
        "articles_fetched": len(articles),
        "articles_in_window": len(in_window),
        "articles_undated": undated,
        "clusters": len(clusters),
        "latest_article_kst": max(
            (a.published for a in in_window if a.published), default=None
        ),
    }
    if meta["latest_article_kst"]:
        meta["latest_article_kst"] = meta["latest_article_kst"].astimezone(KST).strftime(
            "%Y-%m-%d %H:%M KST"
        )

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.json").write_text(
        json.dumps(
            {
                "meta": meta,
                "coverage": coverage,
                "events": [
                    {
                        "id": c.lead.cluster_id,
                        "category": c.lead.category,
                        "prescore": c.score,
                        "topics": c.topics,
                        "lead": c.lead.to_dict(),
                        "duplicate_count": len(c.sources) - 1,
                        "sources": c.sources,
                        "has_primary_source": c.has_primary_source,
                        "articles": [a.to_dict() for a in c.articles],
                    }
                    for c in clusters
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "candidates.md").write_text(
        render_markdown(by_category, meta, coverage, args.max_per_category), encoding="utf-8"
    )

    print(f"수집 구간: {meta['window_start_kst']} ~ {meta['window_end_kst']} ({window_label})")
    print(f"피드 정상 {meta['feeds_ok']}/{meta['feeds_total']}")
    print(f"기사 {meta['articles_fetched']}건 수집 → 구간 내 {meta['articles_in_window']}건")
    print(f"1차 중복제거 후 후보 사건 {meta['clusters']}건")
    for row in coverage:
        mark = "OK  " if row["searched"] else "FAIL"
        print(
            f"  {mark} [{row['category']}] 피드 {row['feeds_ok']}/{row['feeds_total']} · "
            f"기사 {row['articles_in_window']} · 후보 {row['candidate_events']} · "
            f"원자료보유 {row['with_primary_source']}"
        )
    print(f"\n결과: {out/'candidates.md'}, {out/'candidates.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
