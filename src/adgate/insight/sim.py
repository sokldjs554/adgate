"""합성 캠페인 성과 시뮬레이터. 원인을 *주입*하므로 정답(이상 발생일, 원인, 허용·해로운 액션)이 시뮬레이터에서 기계적으로 나온다.

세계 모델(docs/INSIGHT_SPEC.md 1절): 캠페인 하나가 ``days`` 일 동안 소재 ``n_creatives`` 개로 집행된다. 하루의 지표(노출·클릭·전환·지출·도달·빈도·이탈률)는
일 예산, CPM, 소재별 CTR, 전환율(CVR)과 요일 패턴에서 나오고 이항·로그정규 잡음이 낀다. **사건(incident)** 은 정해진 기간 동안 이 모델의 한 부분을 흔든다.

반사실: 같은 날의 *사건이 없었을 때의 기댓값*(``expected_without``)과 *있을 때의 기댓값*(``expected_with``)을 잡음 없이 함께 계산한다.
정답 이상일은 두 기댓값의 핵심 지표가 10% 이상 벌어진 날이다 — 잡음 때문에 우연히 튄 날은 정답이 아니고, 효과가 작아 묻힌 사건은 정답 이상일이 없다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CAUSES = (
    "budget_cut", "bid_drop", "creative_fatigue", "audience_saturation", "tracking_outage", "landing_degradation", "ad_rejected",
    "seasonal_cpm_spike",
)
Cause = Literal[
    "budget_cut", "bid_drop", "creative_fatigue", "audience_saturation", "tracking_outage", "landing_degradation", "ad_rejected",
    "seasonal_cpm_spike",
]
EVENT_TYPES = ("budget_change", "bid_change", "creative_added", "creative_paused", "ad_rejected", "tracking_change", "landing_change", "campaign_edit")
KEY_METRICS = ("impressions", "clicks", "conversions", "spend", "ctr", "cvr", "cpm")
ANOMALY_REL = 0.10          # 이 이상 벌어진 날이 정답 이상일
BASE_UTIL = 0.97            # 평소 지출 / 일 예산
DEFAULT_EVENT = {"budget_cut": True, "bid_drop": True, "ad_rejected": True}  # 그 밖의 사건은 기본으로 이력을 남기지 않는다(조용한 장애)
NOISE = {"low": 0.03, "mid": 0.06, "high": 0.12}


class CampaignParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: Literal["traffic", "conversion", "app_install", "lead"] = "conversion"
    daily_budget_krw: int = Field(default=100_000, ge=20_000, le=5_000_000)
    base_cpm_krw: int = Field(default=4_500, ge=500, le=30_000)
    base_ctr: float = Field(default=0.012, gt=0.001, lt=0.2)
    base_cvr: float = Field(default=0.03, gt=0.002, lt=0.5)
    n_creatives: int = Field(default=3, ge=2, le=4)
    weekly_pattern: Literal["weekday_heavy", "weekend_heavy", "flat"] = "weekday_heavy"
    noise_level: Literal["low", "mid", "high"] = "mid"


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: Cause
    start_day: int = Field(ge=8)              # 1부터. 앞 7일은 정상으로 둔다(기준선)
    end_day: int | None = None                # None 이면 끝까지
    magnitude: float = Field(gt=0.0, le=1.0)
    creative: int | None = Field(default=None, ge=0, le=3)   # creative_fatigue·ad_rejected 의 대상 소재(0부터)
    primary: bool = False
    event: bool | None = None                 # 원인에 해당하는 변경 이력이 로그에 남는가(None 이면 원인별 기본값)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    type: Literal["budget_change", "bid_change", "creative_added", "creative_paused", "ad_rejected", "tracking_change", "landing_change", "campaign_edit"]
    detail: str = Field(max_length=120)


class IncidentScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    seed: int = Field(ge=0, le=10**9)
    days: int = Field(ge=28, le=56)
    start_weekday: int = Field(default=0, ge=0, le=6)    # 1일차의 요일(0=월)
    campaign: CampaignParams = Field(default_factory=CampaignParams)
    incidents: list[Incident] = Field(default_factory=list, max_length=3)
    distractor_events: list[Event] = Field(default_factory=list, max_length=6)
    note: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _check(self) -> IncidentScenario:
        for i in self.incidents:
            if i.start_day > self.days:
                raise ValueError(f"사건 시작일 {i.start_day} 이 기간 {self.days}일을 넘음")
            if i.end_day is not None and not i.start_day <= i.end_day <= self.days:
                raise ValueError(f"사건 end_day {i.end_day} 는 start_day..days 사이")
            if i.cause in ("creative_fatigue", "ad_rejected"):
                if i.creative is None or i.creative >= self.campaign.n_creatives:
                    raise ValueError(f"{i.cause} 는 대상 소재 creative(0..n_creatives-1)가 필요함")
            elif i.creative is not None:
                raise ValueError(f"{i.cause} 는 creative 를 쓰지 않음")
            if i.cause == "ad_rejected" and i.magnitude != 1.0:
                raise ValueError("ad_rejected 는 magnitude 1.0")
        for e in self.distractor_events:
            if e.day > self.days:
                raise ValueError("이벤트 날짜가 기간 밖")
        if len(self.incidents) > 1 and sum(i.primary for i in self.incidents) != 1:
            raise ValueError("사건이 둘 이상이면 primary 를 정확히 하나 지정")
        if len(self.incidents) == 1:
            self.incidents[0].primary = True
        return self


@dataclass
class Day:
    day: int
    weekday: int
    budget_krw: float
    impressions: float
    clicks: float
    conversions: float
    spend: float
    reach: float
    frequency: float
    bounce_rate: float
    creatives: list[dict[str, float]]

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def cvr(self) -> float:
        return self.conversions / self.clicks if self.clicks else 0.0

    @property
    def cpm(self) -> float:
        return self.spend / self.impressions * 1000 if self.impressions else 0.0

    @property
    def utilization(self) -> float:
        return self.spend / self.budget_krw if self.budget_krw else 0.0

    def metric(self, name: str) -> float:
        return float(getattr(self, name))


@dataclass
class Sim:
    scenario: IncidentScenario
    days: list[Day]
    expected_with: list[Day]
    expected_without: list[Day]
    events: list[Event]


def _weekly(pattern: str, weekday: int) -> tuple[float, float]:
    """(CTR·CVR 배수, CPM 배수). 주말(5·6)에 사람들이 더 클릭·구매하는지."""
    weekend = weekday >= 5
    if pattern == "weekday_heavy":
        return (0.82 if weekend else 1.04, 0.95 if weekend else 1.0)
    if pattern == "weekend_heavy":
        return (1.20 if weekend else 0.94, 1.06 if weekend else 1.0)
    return (1.0, 1.0)


def _active(inc: Incident, day: int) -> bool:
    return inc.start_day <= day and (inc.end_day is None or day <= inc.end_day)


def _ramp(inc: Incident, day: int, span: int = 7) -> float:
    return min(1.0, (day - inc.start_day + 1) / span)


def _creative_quality(seed: int, k: int) -> list[float]:
    r = random.Random(seed * 7919 + 13)
    q = [0.85 + 0.35 * r.random() for _ in range(k)]
    q[0] = max(q[0], 1.1)  # 0번 소재가 가장 낫다(승자). 승자가 거절되면 계정 CTR 이 흔들린다
    return q


def _day_expected(sc: IncidentScenario, day: int, incidents: list[Incident], q: list[float]) -> Day:
    c = sc.campaign
    weekday = (sc.start_weekday + day - 1) % 7
    ctr_mult, cpm_mult = _weekly(c.weekly_pattern, weekday)
    act = [i for i in incidents if _active(i, day)]
    budget = float(c.daily_budget_krw)
    cpm = c.base_cpm_krw * cpm_mult * (1 + 0.002 * day)          # 아주 느린 상승
    util = BASE_UTIL
    freq = 1.3 + 0.012 * day
    bounce = 0.45
    cvr_mult = 1.0
    ctr_k = [c.base_ctr * qk * ctr_mult for qk in q]
    share = [1.0 / c.n_creatives] * c.n_creatives
    for i in act:
        if i.cause == "budget_cut":
            budget *= 1 - i.magnitude
        elif i.cause == "bid_drop":
            util *= 1 - i.magnitude
        elif i.cause == "audience_saturation":
            cpm *= 1 + i.magnitude * _ramp(i, day, 5)
            freq += 0.9 * i.magnitude * _ramp(i, day, 5)
        elif i.cause == "seasonal_cpm_spike":
            cpm *= 1 + i.magnitude
        elif i.cause == "creative_fatigue" and i.creative is not None:
            ctr_k[i.creative] *= 1 - i.magnitude * _ramp(i, day)
            freq += 0.5 * i.magnitude * _ramp(i, day)
        elif i.cause == "tracking_outage":
            cvr_mult *= 1 - i.magnitude
        elif i.cause == "landing_degradation":
            cvr_mult *= 1 - i.magnitude
            bounce += 0.35 * i.magnitude
        elif i.cause == "ad_rejected" and i.creative is not None:
            share[i.creative] = 0.0
    total = sum(share)
    share = [s / total for s in share]
    spend = budget * util
    impressions = spend / cpm * 1000
    creatives: list[dict[str, float]] = []
    clicks = conv = 0.0
    for k in range(c.n_creatives):
        imp_k = impressions * share[k]
        clk_k = imp_k * ctr_k[k]
        cv_k = clk_k * c.base_cvr * ctr_mult * cvr_mult
        creatives.append({"impressions": imp_k, "clicks": clk_k, "conversions": cv_k, "spend": spend * share[k]})
        clicks += clk_k
        conv += cv_k
    return Day(day, weekday, budget, impressions, clicks, conv, spend, impressions / freq, freq, bounce, creatives)


def _noisy(rng: random.Random, e: Day, sigma: float) -> Day:
    """기댓값에 잡음을 얹는다: 노출·지출은 로그정규, 클릭·전환은 이항(정규 근사), 빈도·이탈률은 작은 가우스."""
    util_noise = math.exp(rng.gauss(0, sigma * 0.35))
    impressions = max(0.0, e.impressions * math.exp(rng.gauss(0, sigma)))
    spend = min(e.budget_krw * 1.02, e.spend * util_noise)
    creatives: list[dict[str, float]] = []
    clicks = conv = 0.0
    for ck in e.creatives:
        imp_k = max(0.0, ck["impressions"] * (impressions / e.impressions if e.impressions else 0.0))
        p = ck["clicks"] / ck["impressions"] if ck["impressions"] else 0.0
        clk_k = max(0.0, rng.gauss(imp_k * p, math.sqrt(max(imp_k * p * (1 - p), 0.0)))) if imp_k else 0.0
        q = ck["conversions"] / ck["clicks"] if ck["clicks"] else 0.0
        cv_k = max(0.0, rng.gauss(clk_k * q, math.sqrt(max(clk_k * q * (1 - q), 0.0)))) if clk_k else 0.0
        creatives.append({"impressions": imp_k, "clicks": clk_k, "conversions": cv_k, "spend": spend * (ck["spend"] / e.spend if e.spend else 0.0)})
        clicks += clk_k
        conv += cv_k
    freq = max(1.0, e.frequency + rng.gauss(0, 0.03))
    bounce = min(0.95, max(0.05, e.bounce_rate + rng.gauss(0, 0.015)))
    return Day(e.day, e.weekday, e.budget_krw, impressions, clicks, conv, spend, impressions / freq, freq, bounce, creatives)


def causal_events(sc: IncidentScenario) -> list[Event]:
    """사건에서 나온 변경 이력(``event`` 가 켜진 것만). 장애는 보통 이력이 없다 — 이력이 없는 사건을 진단하는 것이 시험이다."""
    out: list[Event] = []
    c = sc.campaign
    for i in sc.incidents:
        on = DEFAULT_EVENT.get(i.cause, False) if i.event is None else i.event
        if not on:
            continue
        day = max(1, i.start_day - 1) if i.cause in ("budget_cut", "bid_drop") else i.start_day
        if i.cause == "budget_cut":
            out.append(Event(day=day, type="budget_change", detail=f"일 예산 {c.daily_budget_krw:,}원 → {round(c.daily_budget_krw * (1 - i.magnitude), -2):,.0f}원"))
        elif i.cause == "bid_drop":
            out.append(Event(day=day, type="bid_change", detail="목표 CPA 하향"))
        elif i.cause == "ad_rejected":
            out.append(Event(day=day, type="ad_rejected", detail=f"소재 {i.creative} 심사 반려"))
        elif i.cause == "tracking_outage":
            out.append(Event(day=day, type="tracking_change", detail="전환 태그 수정 배포"))
        elif i.cause == "landing_degradation":
            out.append(Event(day=day, type="landing_change", detail="랜딩 페이지 개편 배포"))
        elif i.cause == "creative_fatigue":
            out.append(Event(day=day, type="campaign_edit", detail="소재 교체 없이 3주째 집행"))
        else:
            out.append(Event(day=day, type="campaign_edit", detail=f"{i.cause} 관련 메모"))
    return out


def simulate(sc: IncidentScenario) -> Sim:
    q = _creative_quality(sc.seed, sc.campaign.n_creatives)
    rng = random.Random(sc.seed)
    sigma = NOISE[sc.campaign.noise_level]
    ew = [_day_expected(sc, d, sc.incidents, q) for d in range(1, sc.days + 1)]
    ewo = [_day_expected(sc, d, [], q) for d in range(1, sc.days + 1)]
    days = [_noisy(rng, e, sigma) for e in ew]
    events = sorted([*causal_events(sc), *sc.distractor_events], key=lambda e: (e.day, e.type))
    return Sim(sc, days, ew, ewo, events)


# --------------------------------------------------------------------------- 정답
def anomaly_days(sim: Sim) -> list[int]:
    """사건이 있었을 때와 없었을 때의 기댓값이 핵심 지표(계정 지표와 소재별 노출·클릭)에서 10% 이상 벌어진 날(1부터).

    소재별 지표를 포함하는 이유: 소재 하나가 반려돼도 나머지가 그 몫을 가져가 계정 합계는 거의 그대로일 수 있다 — 소재별로만 보이는 장애도 이상이다.
    """
    out: list[int] = []
    for w, wo in zip(sim.expected_with, sim.expected_without, strict=True):
        dev = max(abs(w.metric(m) - wo.metric(m)) / wo.metric(m) for m in KEY_METRICS if wo.metric(m))
        for cw, cwo in zip(w.creatives, wo.creatives, strict=True):
            for m in ("impressions", "clicks"):
                if cwo[m]:
                    dev = max(dev, abs(cw[m] - cwo[m]) / cwo[m])
        if dev >= ANOMALY_REL:
            out.append(w.day)
    return out


def episodes(days: list[int]) -> list[tuple[int, int]]:
    """이어지는 날짜를 (시작, 끝) 구간으로."""
    out: list[tuple[int, int]] = []
    for d in sorted(days):
        if out and d == out[-1][1] + 1:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


# 원인 → 허용되는 액션 / 해로운 액션(원인이 아닌 곳에 돈을 더 쓰거나 원인과 반대로 움직이는 것). docs/INSIGHT_SPEC.md 3절.
ACTIONS = (
    "raise_budget", "adjust_bid", "rotate_creative", "expand_audience", "fix_tracking", "fix_landing", "fix_rejected_ad", "wait_and_monitor", "no_action",
)
ACCEPTABLE: dict[str, tuple[str, ...]] = {
    "budget_cut": ("raise_budget",),
    "bid_drop": ("adjust_bid",),
    "creative_fatigue": ("rotate_creative",),
    "audience_saturation": ("expand_audience", "rotate_creative"),
    "tracking_outage": ("fix_tracking",),
    "landing_degradation": ("fix_landing",),
    "ad_rejected": ("fix_rejected_ad",),
    "seasonal_cpm_spike": ("wait_and_monitor",),
}
HARMFUL: dict[str, tuple[str, ...]] = {
    "budget_cut": ("fix_tracking", "fix_landing"),
    "bid_drop": ("raise_budget", "fix_tracking"),
    "creative_fatigue": ("raise_budget", "adjust_bid"),
    "audience_saturation": ("raise_budget",),
    "tracking_outage": ("raise_budget", "adjust_bid", "expand_audience", "rotate_creative"),
    "landing_degradation": ("raise_budget", "adjust_bid", "expand_audience"),
    "ad_rejected": ("raise_budget", "adjust_bid"),
    "seasonal_cpm_spike": ("raise_budget", "adjust_bid", "expand_audience"),
}


def gold(sim: Sim) -> dict[str, Any]:
    days = anomaly_days(sim)
    active = [i for i in sim.scenario.incidents if any(_active(i, d) for d in days)]
    primary = next((i for i in active if i.primary), active[0] if active else None)
    return {
        "anomaly_days": days, "episodes": episodes(days), "onset": days[0] if days else None,
        "causes": sorted({i.cause for i in active}), "primary_cause": primary.cause if primary else None,
        "acceptable_actions": sorted({a for i in active for a in ACCEPTABLE[i.cause]}),
        "harmful_actions": sorted({a for i in active for a in HARMFUL[i.cause]} - {a for i in active for a in ACCEPTABLE[i.cause]}),
        "null": not days,
    }
