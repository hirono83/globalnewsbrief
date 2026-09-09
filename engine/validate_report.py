#!/usr/bin/env python3
"""완성된 브리핑이 v4.0의 고정 출력 구조와 행동 경계를 지켰는지 검사한다.

구조가 깨진 브리핑은 커밋·발송하지 않는다. 검사 항목은 모두 v4.0 본문에서 직접
가져온 것이며, 내용의 품질이 아니라 규칙 준수만 본다.

    python3 engine/validate_report.py reports/2026-09-09.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CATEGORIES = [
    "글로벌", "미국", "유럽", "중국", "일본", "한국",
    "암호화폐", "채권·금리", "원자재", "IT·테크",
]

SECTIONS = [
    "## 6-1. 오늘의 한 문장",
    "## 6-2. 핵심 사건 5",
    "## 6-3. 카테고리별 글로벌 브리프",
    "## 6-4. 시장 전달경로",
    "## 6-5. PEST Quick View",
    "## 6-6. 반대 근거와 불확실성",
    "## 6-7. 다음 확인 일정",
    "## 6-8. Coverage Audit",
]

MARKET_STATES = ("우호", "혼조", "부담", "판단 불가")

# v4.0 2-5 행동 경계. 투자 지시로 읽힐 수 있는 표현을 본문에서 금지한다.
FORBIDDEN = [
    (r"매수\s*(추천|의견|권고)|매도\s*(추천|의견|권고)", "매수·매도 의견"),
    # 한글은 낱말 경계(\b)가 잡히지 않으므로 긴 표현부터 나열한다.
    (r"목표\s*(?:가격|주가|단가|가)", "목표가격 제시"),
    (r"비중\s*(확대|축소|조절)", "포지션 비중 제시"),
    (r"\b(aggressive|defensive)\b", "투자 지시형 스탠스"),
    (r"수익\s*(보장|확정)", "수익 보장"),
    (r"(사야|팔아야)\s*(한다|합니다)", "매수·매도 지시"),
]

AUDIT_FIELDS = [
    "수집 구간",
    "검색 완료 카테고리",
    "발견 기사 수",
    "중복 제거 후 고유 사건 수",
    "최종 채택 수",
    "상위 5건 선정 근거",
    "제외 수와 주요 사유",
    "공식·직접 원자료 연결 건수",
    "확인 불가 또는 유료벽 뉴스",
    "가장 최신 사건 시각",
]

EVENT_HEADING_RE = re.compile(r"^###\s+#(\d+)\s+(.+)$", re.MULTILINE)
EVENT_REF_RE = re.compile(r"#(\d+)")


def validate(text: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    # 1. 고정 섹션이 모두, 정해진 순서로 있는가
    positions = []
    for section in SECTIONS:
        index = text.find(section)
        if index == -1:
            errors.append(f"필수 섹션 누락: {section}")
        else:
            positions.append((index, section))
    if len(positions) == len(SECTIONS) and positions != sorted(positions):
        errors.append("섹션 순서가 고정 출력 구조와 다릅니다.")

    # 2. 시장 환경은 정해진 네 값 중 하나만
    match = re.search(r"시장\s*환경\s*[:：]\s*(.+)", text)
    if not match:
        errors.append("6-1에 '시장 환경:' 값이 없습니다.")
    else:
        value = match.group(1).strip().strip("*`").strip()
        if value not in MARKET_STATES:
            errors.append(
                f"시장 환경 값이 허용된 네 가지가 아닙니다: {value!r} "
                f"(허용: {' / '.join(MARKET_STATES)})"
            )
    for label in ("지배 변수", "유효기간"):
        if not re.search(rf"{label}\s*[:：]", text):
            errors.append(f"6-1에 '{label}:' 항목이 없습니다.")

    # 3. 10개 카테고리가 모두 출력됐는가
    brief_start = text.find("## 6-3.")
    brief_end = text.find("## 6-4.")
    brief = text[brief_start:brief_end] if brief_start != -1 < brief_end else ""
    for category in CATEGORIES:
        if f"[{category}]" not in brief:
            errors.append(f"6-3에 [{category}] 카테고리가 없습니다.")

    # 4. 핵심 사건 5: 개수와 필수 항목
    core_start = text.find("## 6-2.")
    core_end = text.find("## 6-3.")
    core = text[core_start:core_end] if core_start != -1 < core_end else ""
    core_events = EVENT_HEADING_RE.findall(core)
    if not core_events:
        errors.append("6-2에 '### #N 제목' 형식의 핵심 사건이 없습니다.")
    if len(core_events) > 5:
        errors.append(f"핵심 사건이 5건을 넘습니다: {len(core_events)}건")
    if 0 < len(core_events) < 5:
        warnings.append(
            f"핵심 사건이 {len(core_events)}건입니다. 중요 사건이 부족한 사유가 "
            f"Coverage Audit에 적혀 있는지 확인하세요."
        )

    blocks = re.split(r"^###\s+#\d+\s+", core, flags=re.MULTILINE)[1:]
    for number, block in zip((n for n, _ in core_events), blocks):
        for label in ("[사실]", "[해석]", "[불확실]", "시각:", "출처:"):
            if label not in block:
                errors.append(f"핵심 사건 #{number}에 '{label}' 항목이 없습니다.")
        if not re.search(r"\]\(https?://", block):
            errors.append(f"핵심 사건 #{number}에 직접 링크가 없습니다.")

    # 5. Event ID 규칙: 전역 #1~#N, 중복 없이 한 번씩 부여, 상한 20
    all_ids = sorted({int(n) for n in EVENT_REF_RE.findall(text) if n.isdigit()})
    adopted = [i for i in all_ids if 1 <= i <= 20]
    if len(core_events) != len({n for n, _ in core_events}):
        errors.append("6-2의 Event ID가 중복됩니다.")
    over = [i for i in all_ids if i > 20]
    if over:
        errors.append(f"Event ID가 20을 넘습니다: {over} (v4.0은 20건 상한)")
    if adopted and adopted != list(range(1, len(adopted) + 1)):
        missing = sorted(set(range(1, max(adopted) + 1)) - set(adopted))
        errors.append(f"Event ID가 #1부터 연속이 아닙니다. 빠진 번호: {missing}")

    # 6. 행동 경계
    for pattern, label in FORBIDDEN:
        found = re.search(pattern, text, re.IGNORECASE)
        if found:
            errors.append(f"행동 경계 위반({label}): {found.group(0)!r}")

    # 7. Coverage Audit 필수 항목
    audit = text[text.find("## 6-8.") :] if "## 6-8." in text else ""
    for label in AUDIT_FIELDS:
        if label not in audit:
            errors.append(f"Coverage Audit에 '{label}' 항목이 없습니다.")
    audit_match = re.search(r"최종 채택 수\s*[:：]\s*(\d+)\s*/\s*20", audit)
    if audit_match:
        declared = int(audit_match.group(1))
        if declared > 20:
            errors.append(f"최종 채택 수가 20을 넘습니다: {declared}")
        if adopted and declared != len(adopted):
            warnings.append(
                f"Coverage Audit의 최종 채택 수({declared})와 본문에 등장한 "
                f"Event ID 개수({len(adopted)})가 다릅니다."
            )
    else:
        errors.append("Coverage Audit의 '최종 채택 수'가 'X/20' 형식이 아닙니다.")

    # 8. 최소한의 출처 링크
    links = re.findall(r"\]\((https?://[^\s)]+)\)", text)
    if len(links) < max(len(adopted), 1):
        warnings.append(
            f"링크 {len(links)}개 < 채택 사건 {len(adopted)}건. 모든 채택 뉴스에 "
            f"직접 링크가 필요합니다."
        )

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="브리핑 고정 출력 구조 검증")
    parser.add_argument("report")
    parser.add_argument(
        "--strict", action="store_true", help="경고도 실패로 처리"
    )
    args = parser.parse_args()

    path = Path(args.report)
    if not path.is_file() or not path.stat().st_size:
        print(f"파일이 없거나 비어 있습니다: {path}", file=sys.stderr)
        return 2

    errors, warnings = validate(path.read_text(encoding="utf-8"))
    for warning in warnings:
        print(f"  경고  {warning}")
    for error in errors:
        print(f"  오류  {error}")

    if errors:
        print(f"\n검증 실패: 오류 {len(errors)}건, 경고 {len(warnings)}건")
        return 1
    if warnings and args.strict:
        print(f"\n검증 실패(strict): 경고 {len(warnings)}건")
        return 1
    print(f"\n검증 통과 (경고 {len(warnings)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
