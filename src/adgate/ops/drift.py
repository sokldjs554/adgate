"""입력 분포 이동(드리프트) 모니터. 정답 라벨을 보지 않고(label-free) 엔진 출력과 입력 메타데이터만으로 하루 단위 신호를 만들어 기준 창과 견준다.

신호(하루):
* ``reject_rate`` · ``review_rate`` — 판정 비율
* ``finance_share`` — 대출·투자 업종 비율
* ``hidden_char_rate`` — 보이지 않는 문자·방향 제어 문자가 발견된 소재 비율(우회 표기의 신호)
* ``findings_per_item`` — 소재당 발견 수(연속값)
* ``headline_len`` — 제목 길이(연속값)

탐지: 처음 ``ref_days`` 일을 기준 창으로 묶고, 그 뒤 하루마다 신호별 z 를 구한다(비율은 합친 분산, 설계 효과 ``deff`` 로 광고주 군집을 보수적으로 반영 / 연속값은 Welch).
어떤 신호든 ``|z| ≥ z_alert`` 가 ``persist`` 일 연속이면 경보. 순진한 대조군(naive)은 하루 ``|z| ≥ 3`` 이면 경보다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..config import ReviewConfig
from ..eval.dataset import Example
from ..pipeline import review
from .traffic import FINANCE, DriftSpec, Req, make_stream

PROPORTIONS = ("reject_rate", "review_rate", "finance_share", "hidden_char_rate")
CONTINUOUS = ("findings_per_item", "headline_len")
SIGNALS = (*PROPORTIONS, *CONTINUOUS)


@dataclass(frozen=True)
class DriftConfig:
    ref_days: int = 7
    z_alert: float = 4.0
    persist: int = 2
    deff: float = 1.5       # 광고주 군집·반복 소재로 독립 표본이 줄어드는 것을 반영하는 설계 효과
    min_n: int = 30


DEFAULT = DriftConfig()
NAIVE = DriftConfig(z_alert=3.0, persist=1, deff=1.0)


@dataclass(frozen=True)
class Observed:
    """소재 하나의 label-free 관측(엔진을 한 번 돌린 결과). 결정론이라 같은 소재는 캐시한다."""

    reject: bool
    review: bool
    finance: bool
    hidden: bool
    findings: int
    headline_len: int


def observe(pool: Sequence[Example], cfg: ReviewConfig | None = None) -> dict[str, Observed]:
    cfg = cfg or ReviewConfig()
    out: dict[str, Observed] = {}
    for e in pool:
        r = review(e.creative, cfg)
        out[e.id] = Observed(
            reject=r.outcome == "REJECT", review=r.outcome == "REVIEW", finance=e.creative.advertiser.category in FINANCE,
            hidden=any(f.code == "hidden_characters" for f in r.findings), findings=sum(f.severity != "info" for f in r.findings),
            headline_len=len(e.creative.headline),
        )
    return out


@dataclass
class Day:
    day: int
    n: int = 0
    x: dict[str, int] = field(default_factory=lambda: {k: 0 for k in PROPORTIONS})
    sums: dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in CONTINUOUS})
    sqs: dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in CONTINUOUS})


def summarize(stream: Sequence[Req], obs: dict[str, Observed], days: int) -> list[Day]:
    table = [Day(d) for d in range(days)]
    for r in stream:
        o, d = obs[r.example.id], table[r.day]
        d.n += 1
        d.x["reject_rate"] += o.reject
        d.x["review_rate"] += o.review
        d.x["finance_share"] += o.finance
        d.x["hidden_char_rate"] += o.hidden
        for k, v in (("findings_per_item", o.findings), ("headline_len", o.headline_len)):
            d.sums[k] += v
            d.sqs[k] += v * v
    return table


def _z_prop(x: int, n: int, xr: int, nr: int, deff: float) -> float:
    p, pr = x / n, xr / nr
    pooled = (x + xr) / (n + nr)
    var = pooled * (1 - pooled) * (1 / n + 1 / nr) * deff
    if var <= 0:
        return 0.0 if p == pr else math.copysign(99.0, p - pr)
    return (p - pr) / math.sqrt(var)


def _z_cont(s: float, q: float, n: int, sr: float, qr: float, nr: int, deff: float) -> float:
    m, mr = s / n, sr / nr
    v = max(q / n - m * m, 0.0) * n / max(n - 1, 1)
    vr = max(qr / nr - mr * mr, 0.0) * nr / max(nr - 1, 1)
    se = math.sqrt(deff * (v / n + vr / nr))
    if se <= 0:
        return 0.0 if m == mr else math.copysign(99.0, m - mr)
    return (m - mr) / se


@dataclass
class DriftResult:
    alarm_day: int | None = None
    signal: str | None = None
    max_z_by_day: list[float] = field(default_factory=list)


def detect(table: Sequence[Day], cfg: DriftConfig = DEFAULT) -> DriftResult:
    ref = table[: cfg.ref_days]
    nr = sum(d.n for d in ref)
    if nr < cfg.min_n:
        raise ValueError("기준 창의 표본이 너무 적다")
    xr = {k: sum(d.x[k] for d in ref) for k in PROPORTIONS}
    sr = {k: sum(d.sums[k] for d in ref) for k in CONTINUOUS}
    qr = {k: sum(d.sqs[k] for d in ref) for k in CONTINUOUS}
    res = DriftResult()
    streak = 0
    for d in table[cfg.ref_days:]:
        if d.n < cfg.min_n:
            res.max_z_by_day.append(0.0)
            streak = 0
            continue
        zs = {k: _z_prop(d.x[k], d.n, xr[k], nr, cfg.deff) for k in PROPORTIONS}
        zs.update({k: _z_cont(d.sums[k], d.sqs[k], d.n, sr[k], qr[k], nr, cfg.deff) for k in CONTINUOUS})
        top = max(zs, key=lambda k: abs(zs[k]))
        res.max_z_by_day.append(round(abs(zs[top]), 3))
        streak = streak + 1 if abs(zs[top]) >= cfg.z_alert else 0
        if streak >= cfg.persist and res.alarm_day is None:
            res.alarm_day, res.signal = d.day, top
    return res


# --- 평가 ---------------------------------------------------------------------------------------------------------------------------


def evaluate_drift(
    pool: Sequence[Example], *, seeds: int = 30, days: int = 35, volumes: Sequence[int] = (200, 1000), magnitudes: Sequence[float] = (0.1, 0.25, 0.5),
    start_day: int = 14, kinds: Sequence[str] = ("category_shift", "attack_wave", "obfuscation_surge"),
) -> dict[str, object]:
    """사건 없는 스트림의 오경보와, 이동을 넣은 스트림의 검출 지연을 두 모니터(system/naive)로 잰다. 같은 스트림을 두 모니터가 본다."""
    obs = observe(pool)
    arms = {"system": DEFAULT, "naive": NAIVE}
    null: dict[str, dict[str, dict[str, int]]] = {a: {str(v): {"streams": seeds, "with_alarm": 0} for v in volumes} for a in arms}
    drift: dict[str, dict[str, dict[str, object]]] = {a: {} for a in arms}
    for vol in volumes:
        for s in range(seeds):
            table = summarize(make_stream(pool, days=days, per_day=vol, seed=10_000 + s), obs, days)
            for a, cfg in arms.items():
                null[a][str(vol)]["with_alarm"] += detect(table, cfg).alarm_day is not None
    for kind in kinds:
        for vol in volumes:
            for mag in magnitudes:
                delays: dict[str, list[int]] = {a: [] for a in arms}
                missed = dict.fromkeys(arms, 0)
                early = dict.fromkeys(arms, 0)
                for s in range(seeds):
                    spec = DriftSpec(kind, start_day, mag)  # type: ignore[arg-type]
                    table = summarize(make_stream(pool, days=days, per_day=vol, seed=20_000 + s, drift=spec), obs, days)
                    for a, cfg in arms.items():
                        day = detect(table, cfg).alarm_day
                        if day is None:
                            missed[a] += 1
                        elif day < start_day:
                            early[a] += 1       # 이동 전에 울린 경보는 오경보다
                        else:
                            delays[a].append(day - start_day)
                for a in arms:
                    d = sorted(delays[a])
                    drift[a][f"{kind}/{vol}/{mag}"] = {
                        "streams": seeds, "detected": len(d), "missed": missed[a], "early_false_alarm": early[a],
                        "delay_median": statistics.median(d) if d else None, "delay_p90": d[min(len(d) - 1, math.ceil(0.9 * len(d)) - 1)] if d else None,
                    }
    return {
        "seeds": seeds, "days": days, "start_day": start_day,
        "arms": {a: {"null": null[a], "drift": drift[a], "config": {"z_alert": c.z_alert, "persist": c.persist, "deff": c.deff, "ref_days": c.ref_days}} for a, c in arms.items()},
    }
