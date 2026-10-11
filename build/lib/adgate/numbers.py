"""광고 문구 속 수치 주장(가격·비율·기간)을 뽑고 랜딩 페이지와 대조한다.

값은 종류(``won`` · ``pct`` · ``time``)별로 비교한다. 5만원 = 50,000원, 5천만원 = 50,000,000원.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MONEY = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(억|천만|만|천|백)?\s*원")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_TIME = re.compile(r"(\d+)\s*(개월|달|일)")
_MULT = {None: 1, "": 1, "백": 100, "천": 1_000, "만": 10_000, "천만": 10_000_000, "억": 100_000_000}


@dataclass(frozen=True)
class Claim:
    kind: str                  # won | pct | time
    value: tuple[object, ...]  # 비교용 정규화 값
    start: int                 # soft 문자열 안의 위치
    end: int


def extract(soft: str) -> list[Claim]:
    claims: list[Claim] = []
    for m in _MONEY.finditer(soft):
        digits = float(m.group(1).replace(",", ""))
        value = round(digits * _MULT[m.group(2)])
        claims.append(Claim("won", (value,), m.start(), m.end()))
    for m in _PCT.finditer(soft):
        claims.append(Claim("pct", (round(float(m.group(1)), 6),), m.start(), m.end()))
    for m in _TIME.finditer(soft):
        unit = {"달": "개월"}.get(m.group(2), m.group(2))
        if unit == "일" and soft[: m.start()].rstrip().endswith("월"):
            continue  # 10월 31일 같은 날짜는 기간 주장이 아니다
        claims.append(Claim("time", (int(m.group(1)), unit), m.start(), m.end()))
    claims.sort(key=lambda c: c.start)
    return claims


def landing_values(soft: str) -> dict[str, set[tuple[object, ...]]]:
    values: dict[str, set[tuple[object, ...]]] = {"won": set(), "pct": set(), "time": set()}
    for c in extract(soft):
        values[c.kind].add(c.value)
    return values
