"""RSS/Atom 수집·파싱 유틸리티. 표준 라이브러리만 사용한다.

외부 패키지에 의존하지 않는 이유: 브리핑은 매일 새로 만들어지는 컨테이너에서
실행되므로, pip 설치 실패가 곧 브리핑 실패가 된다.
"""

from __future__ import annotations

import gzip
import html
import io
import re
import shutil
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

KST = timezone(timedelta(hours=9))
UTC = timezone.utc

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36 globalnewsbrief/1.0"
)

# 피드 XML에서 만나는 네임스페이스. 태그 비교는 로컬명으로 하므로 참고용이다.
_TAG_RE = re.compile(r"\{[^}]*\}")
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# 링크에서 제거할 추적 파라미터. 같은 기사가 다른 utm으로 중복 계산되는 것을 막는다.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_brand", "utm_social",
    "fbclid", "gclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "ref", "ref_src", "cmpid", "smid", "partner", "yclid", "at_medium",
    "at_campaign", "__twitter_impression", "guccounter", "sh",
}


class FeedError(Exception):
    """피드를 가져오거나 파싱하지 못했을 때."""


@dataclass
class Article:
    title: str
    link: str
    summary: str
    published: datetime | None
    source_name: str
    source_tier: int
    category: str
    feed_url: str
    # 1차 중복제거 결과에서 채워진다.
    cluster_id: int | None = None
    duplicates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "link": self.link,
            "summary": self.summary,
            "published": self.published.isoformat() if self.published else None,
            "published_kst": (
                self.published.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
                if self.published
                else None
            ),
            "source_name": self.source_name,
            "source_tier": self.source_tier,
            "category": self.category,
            "feed_url": self.feed_url,
            "cluster_id": self.cluster_id,
            "duplicates": self.duplicates,
        }


def _localname(tag: str) -> str:
    return _TAG_RE.sub("", tag)


def clean_text(raw: str | None, limit: int = 400) -> str:
    """HTML 조각이 섞인 요약문을 한 줄 평문으로 정리한다."""
    if not raw:
        return ""
    text = _TAG_STRIP_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def canonical_url(url: str) -> str:
    """추적 파라미터와 프래그먼트를 제거한 비교용 URL."""
    if not url:
        return ""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def parse_date(raw: str | None) -> datetime | None:
    """RFC822 / ISO8601 / 흔한 변종 날짜 문자열을 tz-aware datetime으로."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None

    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError, IndexError):
        pass

    iso = raw.replace("Z", "+00:00")
    # "+0900" 형태를 "+09:00"으로 보정
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)
    for candidate in (iso, iso.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            continue

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _fetch_via_curl(url: str, timeout: int) -> bytes:
    """urllib이 막혔을 때의 폴백.

    일부 매체(예: 한국경제)의 WAF는 User-Agent가 아니라 TLS 지문으로 차단하기
    때문에, 같은 헤더를 보내도 urllib은 403이고 curl은 200이다.
    """
    curl = shutil.which("curl")
    if not curl:
        raise FeedError("urllib 차단, curl 폴백 불가(curl 없음)")
    proc = subprocess.run(
        [curl, "-sS", "-L", "--compressed", "--max-time", str(timeout), "-A", USER_AGENT, url],
        capture_output=True,
        timeout=timeout + 10,
    )
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:120]
        raise FeedError(f"curl 폴백 실패(rc={proc.returncode}): {detail}")
    return proc.stdout


def fetch(url: str, timeout: int = 25, retries: int = 2) -> bytes:
    """피드 본문을 바이트로 가져온다. gzip/deflate를 직접 푼다."""
    last_error: Exception | None = None
    context = ssl.create_default_context()
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                "Accept-Language": "en,ko;q=0.8",
                "Accept-Encoding": "gzip, deflate",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                payload = response.read()
                encoding = (response.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                payload = gzip.GzipFile(fileobj=io.BytesIO(payload)).read()
            elif encoding == "deflate":
                try:
                    payload = zlib.decompress(payload)
                except zlib.error:
                    payload = zlib.decompress(payload, -zlib.MAX_WBITS)
            return payload
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, ssl.SSLError, OSError) as exc:
            last_error = exc
            if attempt == retries:
                break
    try:
        return _fetch_via_curl(url, timeout)
    except (FeedError, subprocess.TimeoutExpired, OSError):
        pass
    raise FeedError(f"{type(last_error).__name__}: {last_error}")


def _first_text(element, names: tuple[str, ...]) -> str | None:
    for child in element:
        if _localname(child.tag) in names:
            if child.text and child.text.strip():
                return child.text
    return None


def _extract_link(element) -> str:
    """RSS의 <link>텍스트와 Atom의 <link href=...>를 모두 처리한다."""
    fallback = ""
    for child in element:
        name = _localname(child.tag)
        if name != "link":
            continue
        href = child.attrib.get("href")
        if href:
            rel = child.attrib.get("rel", "alternate")
            if rel == "alternate":
                return href.strip()
            fallback = fallback or href.strip()
        elif child.text and child.text.strip():
            return child.text.strip()
    if fallback:
        return fallback
    # 일부 피드는 guid에만 URL을 담는다.
    guid = _first_text(element, ("guid", "id"))
    if guid and guid.strip().startswith("http"):
        return guid.strip()
    return ""


def parse_feed(payload: bytes, source_name: str, source_tier: int, category: str, feed_url: str) -> list[Article]:
    """RSS 2.0 / Atom / RDF(RSS 1.0)를 공통 Article 목록으로 변환한다."""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        # 앞쪽에 BOM이나 공백/HTML 주석이 붙은 피드 보정
        text = payload.decode("utf-8", errors="replace").lstrip("﻿ \t\r\n")
        if text[:200].lstrip().lower().startswith(("<!doctype html", "<html")):
            raise FeedError("XML이 아닌 HTML 응답(봇 차단 페이지로 보임)")
        start = text.find("<?xml")
        if start == -1:
            start = min((i for i in (text.find("<rss"), text.find("<feed"), text.find("<rdf:RDF")) if i != -1), default=-1)
        if start <= 0:
            raise FeedError("XML 파싱 실패")
        try:
            root = ElementTree.fromstring(text[start:])
        except ElementTree.ParseError as exc:
            raise FeedError(f"XML 파싱 실패: {exc}") from exc

    if _localname(root.tag).lower() == "html":
        raise FeedError("XML이 아닌 HTML 응답(봇 차단 페이지로 보임)")

    entries = [node for node in root.iter() if _localname(node.tag) in ("item", "entry")]

    articles: list[Article] = []
    for entry in entries:
        title = clean_text(_first_text(entry, ("title",)), limit=300)
        link = _extract_link(entry)
        if not title or not link:
            continue
        summary = clean_text(
            _first_text(entry, ("description", "summary", "content", "encoded", "subtitle"))
        )
        published = parse_date(
            _first_text(entry, ("pubDate", "published", "date", "updated", "created", "issued"))
        )
        articles.append(
            Article(
                title=title,
                link=link.strip(),
                summary=summary,
                published=published,
                source_name=source_name,
                source_tier=source_tier,
                category=category,
                feed_url=feed_url,
            )
        )
    return articles
