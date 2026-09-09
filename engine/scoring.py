"""후보 사건의 기계적 사전정렬 신호.

주의: 이것은 중요도 판정이 아니다. LLM 단계가 v4.0의 네 요소(시장 영향 범위 /
예상 밖 정도 / 지속 가능성 / 실제 가격 반응)로 중요도를 판단하기 전에, 수백 건의
후보를 읽는 순서만 정한다. 이 점수는 브리핑 본문에 출력하지 않는다.

신호는 모두 사람 판단이 필요 없는 것만 쓴다.
  - 출처 티어: 공식 원자료일수록 높다 (v4.0 2-2)
  - 중복 보도 매체 수: 여러 매체가 동시에 다루면 사건일 확률이 높다
  - 시장 관련 어휘 포함 여부
  - 최신성
"""

from __future__ import annotations

import re
from datetime import datetime

# 시장 전달경로가 있는 사건에 흔히 등장하는 어휘. 가중치는 주제군 단위로만 준다.
MARKET_TERMS: dict[str, tuple[str, ...]] = {
    "통화정책": (
        "금리", "기준금리", "통화정책", "연준", "한은", "한국은행", "물가", "인플레",
        "fed", "fomc", "ecb", "boj", "central bank", "monetary policy", "rate cut",
        "rate hike", "interest rate", "inflation", "cpi", "ppi", "yield", "treasury",
        "bond", "quantitative", "policy rate", "deflation",
    ),
    "지정학": (
        "관세", "제재", "수출규제", "무역협정", "무역전쟁", "전쟁", "휴전", "선거",
        "tariff", "sanction", "export control", "trade deal", "trade war", "embargo",
        "ceasefire", "election", "geopolit", "military strike", "blockade",
    ),
    "성장고용": (
        "gdp", "성장률", "고용", "실업", "취업자", "경기침체", "경기",
        "payroll", "unemployment", "jobless", "recession", "growth forecast",
        "industrial production", "retail sales", "pmi", "consumer confidence",
    ),
    "기업시장": (
        "실적", "영업이익", "공시", "상장", "인수", "합병", "감자", "증자", "주가",
        "코스피", "코스닥", "나스닥", "다우", "earnings", "guidance", "revenue",
        "ipo", "merger", "acquisition", "buyback", "dividend", "bankruptcy",
        "default", "downgrade", "upgrade", "s&p 500", "nasdaq", "stock market",
    ),
    "원자재": (
        "유가", "원유", "천연가스", "금값", "구리", "곡물",
        "opec", "crude", "oil price", "natural gas", "lng", "gold price", "copper",
        "wheat", "commodit", "refinery", "pipeline", "output cut",
    ),
    "암호화폐": (
        "비트코인", "이더리움", "가상자산", "스테이블코인",
        "bitcoin", "ethereum", "crypto", "stablecoin", "spot etf", "sec approval",
        "exchange hack", "tokeniz",
    ),
    "기술": (
        "반도체", "인공지능", "데이터센터", "파운드리", "메모리",
        "semiconductor", "chip", "foundry", "nvidia", "tsmc", "artificial intelligence",
        " ai ", "data center", "cloud capex", "euv", "hbm", "antitrust",
    ),
    "외환": (
        "환율", "원화", "달러", "엔화", "위안화", "외환",
        "dollar index", "exchange rate", "currency", "yen", "yuan", "forex",
        "devaluation", "intervention",
    ),
}

# 시장 브리핑과 무관해 뒤로 미룰 주제. 삭제가 아니라 후순위로만 내린다.
NOISE_TERMS: tuple[str, ...] = (
    "프로야구", "축구", "야구", "올림픽", "월드컵", "리그", "감독 선임", "홈런",
    "아이돌", "드라마", "예능", "배우", "가수", "영화제", "컴백", "결혼", "이혼",
    "레시피", "맛집", "여행기", "운세", "부고", "인사말",
    "football", "soccer", "basketball", "baseball", "olympic", "world cup",
    "celebrity", "k-pop", "movie review", "tv show", "recipe", "horoscope",
    "obituary", "best deals", "deal of the day", "gift guide", "how to watch",
)

_AI_RE = re.compile(r"\bai\b", re.IGNORECASE)


def _haystack(title: str, summary: str) -> str:
    return f" {title} {summary} ".lower()


def topic_hits(title: str, summary: str) -> list[str]:
    """제목·요약에서 확인된 시장 주제군 목록."""
    text = _haystack(title, summary)
    hits = []
    for topic, terms in MARKET_TERMS.items():
        for term in terms:
            if term == " ai ":
                if _AI_RE.search(text):
                    hits.append(topic)
                    break
            elif term in text:
                hits.append(topic)
                break
    return hits


def is_noise(title: str, summary: str) -> bool:
    text = _haystack(title, summary)
    return any(term in text for term in NOISE_TERMS)


def prescore(
    *,
    tier: int,
    outlet_count: int,
    title: str,
    summary: str,
    published: datetime | None,
    now: datetime,
) -> tuple[float, list[str]]:
    """(정렬 점수, 확인된 주제군). 점수는 순서를 정하는 용도로만 쓴다."""
    score = 0.0

    # 출처 우선순위 (v4.0 2-2)
    score += {1: 3.0, 2: 2.0, 3: 1.0}.get(tier, 0.5)

    # 여러 매체가 같은 사건을 다루면 사건일 가능성이 높다. 상한을 둬서 연예·스포츠
    # 기사가 보도량만으로 상위에 오르지 않게 한다.
    score += min(outlet_count - 1, 4) * 1.25

    topics = topic_hits(title, summary)
    if topics:
        score += min(len(topics), 3) * 1.5
    else:
        # 시장 관련 어휘가 전혀 없는 기사는 후순위로 내린다. 고유명사 블랙리스트를
        # 늘리는 것보다 이 규칙이 스포츠·연예·생활 기사를 훨씬 넓게 잡는다.
        score -= 3.0

    if published is not None:
        hours = max((now - published).total_seconds() / 3600.0, 0.0)
        score += max(0.0, 2.0 - hours / 12.0)
    else:
        # 시각을 모르면 핵심 사건으로 승격할 수 없으므로(v4.0 실패 처리) 낮춘다.
        score -= 1.5

    if is_noise(title, summary):
        score -= 6.0

    return round(score, 2), topics
