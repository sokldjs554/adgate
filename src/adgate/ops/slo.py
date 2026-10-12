"""SLO·에러 버짓과 번레이트(burn-rate) 경보. 장애를 주입한 합성 시계열로 경보 정책이 장애를 *얼마나 빨리* 알아채고 *순간 흔들림에는 조용한지* 잰다.

* SLI: 요청 중 성공(5xx·시간 초과가 아님)한 비율 / 지연이 한도 안인 비율. SLO: 목표 비율(예 99.9%)과 기간(예 30일). 에러 버짓 = 1 − 목표.
* **번레이트** = (창 안의 실패율) / (에러 버짓). 1 이면 버짓을 정확히 기간 끝에 다 쓴다.
* ``burn`` 정책(다중 창 · 다중 번레이트, SRE 워크북의 권장 조합): **page** = (1시간·5분 창이 모두 14.4× 이상) 또는 (6시간·30분 창이 모두 6× 이상),
  **ticket** = (1일·2시간 창이 모두 3× 이상) 또는 (3일·6시간 창이 모두 1× 이상). 긴 창이 '정말 많이 썼는가', 짧은 창이 '지금도 쓰고 있는가'를 본다.
* ``naive`` 정책(대조군): page = 5분 실패율 > 1%, ticket = 1시간 실패율 > 0.5%.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MINUTES_PER_DAY = 1440


@dataclass(frozen=True)
class SLO:
    name: str
    objective: float = 0.999      # 목표 성공률
    window_days: int = 30

    @property
    def budget(self) -> float:
        return 1.0 - self.objective


@dataclass(frozen=True)
class Incident:
    name: str
    start_min: int
    minutes: int
    error_rate: float
    should_page: bool            # 사람을 깨워야 하는 장애인가
    should_ticket: bool = True   # 최소한 티켓은 열려야 하는가


Group = tuple[tuple[int, float], ...]      # (창 분, 번레이트 문턱)들 — 한 묶음 안에서는 모두 만족해야 한다


@dataclass(frozen=True)
class AlertPolicy:
    name: str
    page: tuple[Group, ...]                 # 묶음 중 하나라도 만족하면 켜진다
    ticket: tuple[Group, ...]


BURN = AlertPolicy(
    "burn",
    page=(((60, 14.4), (5, 14.4)), ((360, 6.0), (30, 6.0))),
    ticket=(((1440, 3.0), (120, 3.0)), ((4320, 1.0), (360, 1.0))),
)
NAIVE = AlertPolicy("naive", page=(((5, 10.0),),), ticket=(((60, 5.0),),))   # 번레이트 환산: 5분 실패율 1%(=10×), 1시간 0.5%(=5×)


def simulate(
    *, days: int, seed: int, incidents: Sequence[Incident], rpm: float, base_error: float = 0.0002,
) -> tuple[list[int], list[int]]:
    """분 단위 (요청 수, 실패 수). 요청은 하루 주기(낮이 많음)를 따르는 푸아송, 실패는 이항(기본 실패율 + 장애 구간의 실패율)."""
    rng = random.Random(seed)
    n: list[int] = []
    e: list[int] = []
    for t in range(days * MINUTES_PER_DAY):
        lam = rpm * (1.0 + 0.5 * math.sin(2 * math.pi * ((t % MINUTES_PER_DAY) / MINUTES_PER_DAY - 0.3)))
        k = _poisson(rng, lam)
        p = base_error
        for inc in incidents:
            if inc.start_min <= t < inc.start_min + inc.minutes:
                p = max(p, inc.error_rate)
        n.append(k)
        e.append(_binom(rng, k, p))
    return n, e


def _poisson(rng: random.Random, lam: float) -> int:
    if lam > 30:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _binom(rng: random.Random, n: int, p: float) -> int:
    if n == 0 or p <= 0:
        return 0
    if n * p > 30 and n * (1 - p) > 30:
        return min(n, max(0, round(rng.gauss(n * p, math.sqrt(n * p * (1 - p))))))
    return sum(rng.random() < p for _ in range(n))


def _prefix(xs: Sequence[int]) -> list[int]:
    out = [0]
    for x in xs:
        out.append(out[-1] + x)
    return out


def firing_minutes(n: Sequence[int], e: Sequence[int], slo: SLO, policy: AlertPolicy) -> dict[str, list[bool]]:
    """분마다 page·ticket 경보가 켜져 있는가."""
    pn, pe = _prefix(n), _prefix(e)

    def burn(t: int, w: int) -> float:
        lo = max(0, t + 1 - w)
        reqs = pn[t + 1] - pn[lo]
        return ((pe[t + 1] - pe[lo]) / reqs) / slo.budget if reqs else 0.0

    out: dict[str, list[bool]] = {"page": [], "ticket": []}
    for t in range(len(n)):
        out["page"].append(any(all(burn(t, w) >= thr for w, thr in group) for group in policy.page))
        out["ticket"].append(any(all(burn(t, w) >= thr for w, thr in group) for group in policy.ticket))
    return out


def episodes(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """켜진 구간 [시작, 끝)."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for t, f in enumerate(flags):
        if f and start is None:
            start = t
        if not f and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def score_alerts(fire: dict[str, list[bool]], incidents: Sequence[Incident], grace_min: int = 120) -> dict[str, dict[str, Any]]:
    """장애마다 첫 경보까지의 지연(분), 장애와 상관없이 울린 경보 구간 수(오경보)."""
    res: dict[str, dict[str, Any]] = {}
    for kind in ("page", "ticket"):
        eps = episodes(fire[kind])
        per: dict[str, Any] = {}
        for inc in incidents:
            end = inc.start_min + inc.minutes
            hit = [s for s, _ in eps if inc.start_min <= s < end + grace_min]
            per[inc.name] = {"delay_min": (hit[0] - inc.start_min) if hit else None, "alerts": len(hit)}
        claimed = [(inc.start_min, inc.start_min + inc.minutes + grace_min) for inc in incidents]
        false = [s for s, _ in eps if not any(a <= s < b for a, b in claimed)]
        res[kind] = {"per_incident": per, "false_episodes": len(false), "episodes": len(eps)}
    return res


def budget_spent(n: Sequence[int], e: Sequence[int], slo: SLO, upto_min: int | None = None) -> float:
    """기간 전체 요청 중 실패율 / 에러 버짓 (1 이면 버짓을 다 썼다)."""
    upto = upto_min if upto_min is not None else len(n)
    reqs = sum(n[:upto])
    return (sum(e[:upto]) / reqs) / slo.budget if reqs else 0.0


STANDARD_INCIDENTS = (
    Incident("hard_outage", 3 * MINUTES_PER_DAY + 600, 20, 0.50, should_page=True),
    Incident("partial_degradation", 9 * MINUTES_PER_DAY + 300, 180, 0.02, should_page=True),
    Incident("slow_burn", 14 * MINUTES_PER_DAY, 3 * MINUTES_PER_DAY, 0.004, should_page=False),   # 번레이트 4× 로 사흘: 티켓은 열려야 하지만 새벽에 깨우지는 않는다
    Incident("brief_blip", 22 * MINUTES_PER_DAY + 800, 2, 0.10, should_page=False, should_ticket=False),
)


def evaluate_alerting(
    *, rpms: Sequence[float] = (5, 60, 600), seeds: int = 5, days: int = 28, slo: SLO | None = None,
) -> dict[str, object]:
    slo = slo or SLO("api_availability")
    out: dict[str, object] = {"slo": {"objective": slo.objective, "window_days": slo.window_days}, "days": days, "seeds": seeds, "rows": []}
    rows: list[dict[str, object]] = []
    for policy in (BURN, NAIVE):
        for rpm in rpms:
            agg: dict[str, dict[str, list[int | None]]] = {inc.name: {"page": [], "ticket": []} for inc in STANDARD_INCIDENTS}
            false_page = false_ticket = 0
            for s in range(seeds):
                n, e = simulate(days=days, seed=700 + s, incidents=STANDARD_INCIDENTS, rpm=rpm)
                sc = score_alerts(firing_minutes(n, e, slo, policy), STANDARD_INCIDENTS)
                for kind in ("page", "ticket"):
                    for inc in STANDARD_INCIDENTS:
                        agg[inc.name][kind].append(sc[kind]["per_incident"][inc.name]["delay_min"])
                false_page += sc["page"]["false_episodes"]
                false_ticket += sc["ticket"]["false_episodes"]
            rows.append({"policy": policy.name, "rpm": rpm, "incidents": agg, "false_page_episodes": false_page, "false_ticket_episodes": false_ticket, "streams": seeds})
    out["rows"] = rows
    return out
