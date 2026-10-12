"""합성 트래픽 스트림. 평가셋의 소재(정답을 안다)를 요일 패턴·광고주 분포·분포 이동(드리프트)을 넣어 시간순으로 내보낸다.

정답은 *평가용*이다 — 모니터와 섀도 비교의 label-free 신호는 정답을 보지 않는다. 정답은 사람 확인(스팟체크)을 흉내 낼 때와 채점할 때만 쓴다.
실제 트래픽이 아니다: 소재가 평가셋의 것이라 '새로운 문구'는 없다. 이동은 *혼합*이 바뀌는 것(업종, 위험 소재 비율, 우회 표기 비율)으로만 만든다.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..eval.dataset import Example

DriftKind = Literal["none", "category_shift", "attack_wave", "obfuscation_surge"]
OBFUSCATION_TAGS = frozenset({"evasion", "zero_width", "spacing", "fullwidth", "respelled", "dotted", "phonetic", "split_fields"})
FINANCE = frozenset({"loan", "invest"})


@dataclass(frozen=True)
class DriftSpec:
    kind: DriftKind = "none"
    start_day: int = 14
    magnitude: float = 0.0     # 시작 뒤 요청 중 '이동한 혼합'에서 뽑는 비율(0–1)


@dataclass(frozen=True)
class Req:
    day: int
    t: float                   # 스트림 시작부터의 초
    tenant: str
    example: Example
    drifted: bool              # 이동한 혼합에서 뽑았는가(채점용)


def subset(pool: Sequence[Example], kind: DriftKind) -> list[Example]:
    if kind == "category_shift":
        return [e for e in pool if e.creative.advertiser.category in FINANCE]
    if kind == "attack_wave":
        return [e for e in pool if e.gold.decision != "APPROVE"]
    if kind == "obfuscation_surge":
        return [e for e in pool if OBFUSCATION_TAGS & set(e.gold.tags)]
    return []


def _poisson(rng: random.Random, lam: float) -> int:
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def day_factor(day: int) -> float:
    """요일 패턴: 주말은 평일의 60%. 평균이 1 이 되게 맞춘다."""
    raw = 0.6 if day % 7 in (5, 6) else 1.0
    return raw / ((5 * 1.0 + 2 * 0.6) / 7)


def make_stream(
    pool: Sequence[Example], *, days: int, per_day: float, seed: int, drift: DriftSpec | None = None, n_tenants: int = 20,
) -> list[Req]:
    drift = drift or DriftSpec()
    rng = random.Random(seed)
    shifted = subset(pool, drift.kind)
    if drift.kind != "none" and not shifted:
        raise ValueError(f"{drift.kind}: 이동할 혼합에 소재가 없다")
    weights = [1.0 / (k + 1) for k in range(n_tenants)]
    out: list[Req] = []
    for d in range(days):
        n = _poisson(rng, per_day * day_factor(d))
        for _ in range(n):
            moved = drift.kind != "none" and d >= drift.start_day and rng.random() < drift.magnitude
            ex = rng.choice(shifted) if moved else rng.choice(pool)
            tenant = f"t{rng.choices(range(n_tenants), weights)[0]:02d}"
            out.append(Req(day=d, t=d * 86400 + rng.random() * 86400, tenant=tenant, example=ex, drifted=moved))
    out.sort(key=lambda r: r.t)
    return out
