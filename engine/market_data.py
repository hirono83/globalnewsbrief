#!/usr/bin/env python3
"""검증 가능한 시장 가격 데이터를 As-of와 함께 수집한다.

v4.0은 "가격 데이터와 As-of가 확인되지 않으면 가격 반응을 추정하지 말라"고
반복해서 요구한다. 이 모듈은 그 예외를 만드는 유일한 통로이며, 공식 원자료에서
직접 받은 값만 담는다. 여기 없는 자산의 가격 반응은 브리핑에서 만들어내지 않는다.

현재 수집원: 미국 재무부 일별 국채 수익률 곡선 (공식, 무료, 인증 불필요)

    python3 engine/market_data.py
    python3 engine/market_data.py --out out
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import feedlib

ROOT = Path(__file__).resolve().parent.parent

TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month={month}"
)

# 브리핑에서 실제로 쓰이는 만기만 남긴다.
TENORS = {
    "BC_3MONTH": "3M",
    "BC_2YEAR": "2Y",
    "BC_5YEAR": "5Y",
    "BC_10YEAR": "10Y",
    "BC_30YEAR": "30Y",
}


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def fetch_treasury_curve(now: datetime) -> dict:
    """최근 2영업일의 미 국채 수익률과 전일 대비 변화(bp)."""
    result: dict = {
        "source": "U.S. Department of the Treasury — Daily Treasury Par Yield Curve Rates",
        "source_url": (
            "https://home.treasury.gov/resource-center/data-chart-center/"
            "interest-rates/TextView?type=daily_treasury_yield_curve"
        ),
        "available": False,
        "error": None,
    }
    try:
        payload = feedlib.fetch(TREASURY_URL.format(month=now.strftime("%Y%m")), timeout=30)
        root = ElementTree.fromstring(payload)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return result

    rows: list[tuple[datetime, dict[str, float]]] = []
    for props in root.iter():
        if _local(props.tag) != "properties":
            continue
        values: dict[str, float] = {}
        as_of: datetime | None = None
        for field in props:
            name = _local(field.tag)
            text = (field.text or "").strip()
            if name == "NEW_DATE" and text:
                as_of = feedlib.parse_date(text)
            elif name in TENORS and text:
                try:
                    values[TENORS[name]] = float(text)
                except ValueError:
                    pass
        if as_of and values:
            rows.append((as_of, values))

    if not rows:
        # 월초에는 이번 달 데이터가 아직 비어 있을 수 있다.
        result["error"] = "이번 달 데이터 없음"
        return result

    rows.sort(key=lambda r: r[0])
    latest_date, latest = rows[-1]
    previous = rows[-2] if len(rows) >= 2 else None

    yields = {}
    for tenor in TENORS.values():
        if tenor not in latest:
            continue
        entry = {"percent": latest[tenor], "change_bp": None}
        if previous and tenor in previous[1]:
            entry["change_bp"] = round((latest[tenor] - previous[1][tenor]) * 100, 1)
        yields[tenor] = entry

    result.update(
        {
            "available": True,
            "as_of": latest_date.strftime("%Y-%m-%d"),
            "as_of_note": "미 동부시간 기준 해당 영업일 종가 확정치",
            "compared_with": previous[0].strftime("%Y-%m-%d") if previous else None,
            "yields": yields,
        }
    )
    return result


def render_markdown(curve: dict) -> str:
    lines = ["# 시장 가격 데이터 (As-of 확인됨)", ""]
    if not curve.get("available"):
        lines += [
            "## 미 국채 수익률 곡선",
            "",
            f"- 확인 불가: {curve.get('error')}",
            "- 이 경우 브리핑에서 금리 가격 반응·임계값을 만들지 말고 `가격 확인 필요`로 쓴다.",
            "",
        ]
        return "\n".join(lines)

    lines += [
        "## 미 국채 수익률 곡선",
        "",
        f"- As-of: {curve['as_of']} ({curve['as_of_note']})",
        f"- 전일 비교 기준일: {curve.get('compared_with') or '없음(비교 불가)'}",
        f"- 출처: {curve['source']} — {curve['source_url']}",
        "",
        "| 만기 | 수익률(%) | 전일 대비(bp) |",
        "| --- | --- | --- |",
    ]
    for tenor, entry in curve["yields"].items():
        change = "확인 불가" if entry["change_bp"] is None else f"{entry['change_bp']:+.1f}"
        lines.append(f"| {tenor} | {entry['percent']:.2f} | {change} |")
    lines += [
        "",
        "이 표에 없는 자산(주가지수·환율·원자재·암호화폐 등)은 가격 데이터가 확인되지",
        "않았다. 해당 자산의 가격 반응·반증·돌파 수준을 만들지 말고 `가격 확인 필요`로 쓴다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="out")
    args = parser.parse_args()

    now = datetime.now(feedlib.KST)
    curve = fetch_treasury_curve(now)

    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "market_data.json").write_text(
        json.dumps({"treasury_curve": curve}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "market_data.md").write_text(render_markdown(curve), encoding="utf-8")

    if curve["available"]:
        print(f"미 국채 수익률 As-of {curve['as_of']}")
        for tenor, entry in curve["yields"].items():
            change = "n/a" if entry["change_bp"] is None else f"{entry['change_bp']:+.1f}bp"
            print(f"  {tenor:>3s}  {entry['percent']:.2f}%  ({change})")
    else:
        print(f"가격 데이터 확인 불가: {curve['error']}")
    print(f"\n결과: {out/'market_data.md'}, {out/'market_data.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
