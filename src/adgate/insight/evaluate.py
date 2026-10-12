"""성과 이상 탐지·진단·액션 평가(docs/EVAL_INSIGHT.md). 시나리오 → 시뮬레이터 → 시스템(또는 비교 기준) → 정답과 견줌. 전부 결정론이고 모델이 없다.

팔(arm):

* ``system`` — 다중 창 탐지 + 신호 서명 진단 + 기대효용 액션(``analysis.py``).
* 절제: ``no_history``(변경 이력을 단서로 안 씀), ``no_alignment``(같은 요일로 맞추지 않음), ``no_persistence``(이틀 연속 조건 없음).
* ``naive`` — 흔한 대시보드 규칙: 일 단위로 직전 7일 평균과 20% 넘게 벌어지면 경보, 증상으로 액션을 정한다(지출↓→예산 올리기, CTR↓→소재 교체, 전환율↓→태그 점검, CPM↑→타깃 넓히기).
* ``history_only`` — 변경 이력만 본다: 이력이 찍힌 날 그 이력이 가리키는 원인으로 경보(조용한 장애는 못 보고, 틀린 방향의 이력에는 속는다).

경보는 정답 이상일 구간과 ``[시작−1, 끝+7]`` 안에서 울리면 그 구간을 *잡은 것*이고, 정답 구간 근처(시작−3 ~ 끝+7)와 겹치지 않는 경보는 *오경보*다.
진단은 경보일 + 3일까지의 데이터로(``alert+3``, 인과적)와 전체 데이터로(``final``) 두 번 낸다.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..eval.metrics import percentile, ratio
from .analysis import Observation, detect, diagnose, override, recommend
from .sim import ACCEPTABLE, KEY_METRICS, Event, IncidentScenario, Sim, gold, simulate

CHANGE_ACTIONS = ("raise_budget", "adjust_bid", "rotate_creative", "expand_audience", "fix_tracking", "fix_landing", "fix_rejected_ad")
SAFE_ACTIONS = ("wait_and_monitor", "no_action")
EVENT_CAUSE = {
    "budget_change": "budget_cut", "bid_change": "bid_drop", "ad_rejected": "ad_rejected", "tracking_change": "tracking_outage",
    "landing_change": "landing_degradation",
}
COVER_BEFORE, COVER_AFTER, NEAR_BEFORE, NEAR_AFTER = 1, 7, 3, 7
ARMS = ("system", "no_history", "no_alignment", "no_persistence", "naive", "history_only")


def load_scenarios(path: str | Path) -> list[IncidentScenario]:
    out: list[IncidentScenario] = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                out.append(IncidentScenario.model_validate(json.loads(line)))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"{path}:{n}: {str(exc)[:200]}") from exc
    return out


@dataclass
class Alert:
    start: int                     # 1부터
    end: int
    alert_day: int
    cause: str | None = None       # alert+3 진단의 1순위(없으면 None)
    causes: Sequence[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    explained: bool = False
    final_cause: str | None = None
    final_causes: Sequence[str] = field(default_factory=list)
    final_actions: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- 팔
def run_system(sim: Sim) -> list[Alert]:
    obs = Observation(sim.days, sim.events)
    full = detect(obs)
    alerts: list[Alert] = []
    for ep in full.episodes:
        a = Alert(ep.start, ep.end, ep.alert_day)
        part = detect(obs, upto=min(len(sim.days), ep.alert_day + 3))
        match = next((e for e in part.episodes if e.alert_day == ep.alert_day), None)
        if match is not None:
            dx = diagnose(obs, part, match)
            a.cause, a.causes, a.explained = dx.top, dx.causes, dx.explained
            a.actions = [r["action"] for r in recommend(obs, dx)]
        dxf = diagnose(obs, full, ep)
        a.final_cause, a.final_causes = dxf.top, dxf.causes
        a.final_actions = [r["action"] for r in recommend(obs, dxf)]
        alerts.append(a)
    return alerts


def run_ablation(sim: Sim, **kw: Any) -> list[Alert]:
    with override(**kw):
        return run_system(sim)


def _episodes(days: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for d in sorted(days):
        if out and d <= out[-1][1] + 2:       # 하루 틈은 같은 구간
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def run_naive(sim: Sim) -> list[Alert]:
    hits: list[int] = []
    causes: dict[int, str] = {}
    for t in range(7, len(sim.days)):
        prev = sim.days[t - 7:t]
        sym: dict[str, float] = {}
        for m in KEY_METRICS:
            base = statistics.fmean(d.metric(m) for d in prev)
            if base:
                sym[m] = sim.days[t].metric(m) / base - 1
        if any(abs(v) > 0.20 for v in sym.values()):
            hits.append(t + 1)
            if sym.get("spend", 0) < -0.2:
                causes[t + 1] = "budget_cut"
            elif sym.get("ctr", 0) < -0.2:
                causes[t + 1] = "creative_fatigue"
            elif sym.get("cvr", 0) < -0.2:
                causes[t + 1] = "tracking_outage"
            elif sym.get("cpm", 0) > 0.2:
                causes[t + 1] = "audience_saturation"
    alerts = []
    for s, e in _episodes(hits):
        cause = next((causes[d] for d in range(s, e + 1) if d in causes), None)
        act = list(ACCEPTABLE[cause][:1]) if cause else ["wait_and_monitor"]
        alerts.append(Alert(s, e, s, cause, [cause] if cause else [], act, cause is not None, cause, [cause] if cause else [], act))
    return alerts


def run_history_only(sim: Sim) -> list[Alert]:
    out = []
    for ev in sim.events:
        cause = EVENT_CAUSE.get(ev.type)
        if cause:
            act = list(ACCEPTABLE[cause][:1])
            out.append(Alert(ev.day, ev.day, ev.day, cause, [cause], act, True, cause, [cause], act))
    return out


ARM_RUN: dict[str, Callable[[Sim], list[Alert]]] = {
    "system": run_system, "no_history": lambda s: run_ablation(s, use_history=False), "no_alignment": lambda s: run_ablation(s, align=False),
    "no_persistence": lambda s: run_ablation(s, persistence=False), "naive": run_naive, "history_only": run_history_only,
}


# --------------------------------------------------------------------------- 채점
def episode_causes(sc: IncidentScenario, g: dict[str, Any], span: tuple[int, int]) -> list[str]:
    """정답 구간 ``span`` 에서 활동한(정답 원인에 든) 사건들의 원인."""
    s0, e0 = span
    return sorted({i.cause for i in sc.incidents if i.cause in g["causes"] and i.start_day <= e0 and (i.end_day is None or i.end_day >= s0)})


def _history_kind(sc: IncidentScenario, events: list[Event], g: dict[str, Any]) -> str:
    causal = {EVENT_CAUSE[e.type] for e in events if e.type in EVENT_CAUSE}
    if not sc.incidents:
        return "none_expected" if not causal else "misleading"
    if not causal:
        return "no_history"
    return "correct" if causal & set(g["causes"]) else "misleading"


def score_scenario(sc: IncidentScenario, sim: Sim, alerts: list[Alert]) -> dict[str, Any]:
    g = gold(sim)
    episodes = g["episodes"]
    n_days = len(sim.days)
    covered: list[dict[str, Any]] = []
    used: set[int] = set()
    for s, e in episodes:
        cand = [(k, a) for k, a in enumerate(alerts) if s - COVER_BEFORE <= a.alert_day <= e + COVER_AFTER]
        ep_causes = episode_causes(sc, g, (s, e))
        if not cand:
            covered.append({"gold": [s, e], "detected": False, "causes": ep_causes})
            continue
        k, a = min(cand, key=lambda ka: ka[1].alert_day)
        used.add(k)
        row: dict[str, Any] = {"gold": [s, e], "detected": True, "delay": a.alert_day - s, "alert_day": a.alert_day, "causes": ep_causes, "onset_error": abs(a.start - s)}
        for tag, top, tops in (("alert3", a.cause, a.causes), ("final", a.final_cause, a.final_causes)):
            row[tag] = {
                "top_in_set": top in ep_causes, "top_is_primary": top == g["primary_cause"], "all_found": set(ep_causes) <= set(tops) if len(ep_causes) > 1 else None,
                "top": top, "explained": a.explained if tag == "alert3" else None,
            }
        covered.append(row)
    false_alarms = []
    for k, a in enumerate(alerts):
        if k in used:
            continue
        near = any(s - NEAR_BEFORE <= a.end and a.start <= e + NEAR_AFTER for s, e in episodes)
        if not near:
            false_alarms.append(k)
    harmful = set(g["harmful_actions"])
    per_alert: list[dict[str, Any]] = []
    for k, a in enumerate(alerts):
        is_fa = k in false_alarms
        # 정답이 없는 경보(오경보)와 이상이 없는 시나리오에서는 변경 액션이 모두 해롭다
        h = bool(set(a.actions) & (set(CHANGE_ACTIONS) if (is_fa or g["null"]) else harmful))
        useful = (not is_fa) and (bool(set(a.actions) & set(g["acceptable_actions"])) if not g["null"] else all(x in SAFE_ACTIONS for x in a.actions))
        per_alert.append({"false_alarm": is_fa, "harmful": h, "useful": useful, "actions": a.actions, "cause": a.cause})
    mags = [i.magnitude for i in sc.incidents]
    return {
        "id": sc.id, "null": g["null"], "n_days": n_days, "noise": sc.campaign.noise_level, "pattern": sc.campaign.weekly_pattern, "budget": sc.campaign.daily_budget_krw,
        "incidents": len(sc.incidents), "causes": g["causes"], "primary": g["primary_cause"], "max_magnitude": max(mags) if mags else 0.0,
        "subtle": g["null"] and bool(sc.incidents), "history": _history_kind(sc, sim.events, g),
        "gold_episodes": len(episodes), "episodes": covered, "alerts": len(alerts), "false_alarms": len(false_alarms), "per_alert": per_alert,
    }


# --------------------------------------------------------------------------- 집계
def _bucket(m: float) -> str:
    return "<0.3" if m < 0.3 else ("0.3-0.6" if m < 0.6 else ">=0.6")


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    eps = [e for s in scores for e in s["episodes"]]
    det = [e for e in eps if e["detected"]]
    delays = [e["delay"] for e in det]
    null = [s for s in scores if s["null"]]
    days = sum(s["n_days"] for s in scores)
    alerts = [pa for s in scores for pa in s["per_alert"]]

    def diag(tag: str) -> dict[str, Any]:
        single = [e for e in det if len(e["causes"]) == 1]
        multi = [e for e in det if len(e["causes"]) > 1]
        return {
            "single_top_in_set": ratio(sum(e[tag]["top_in_set"] for e in single), len(single)),
            "multi_top_in_set": ratio(sum(e[tag]["top_in_set"] for e in multi), len(multi)),
            "multi_top_is_primary": ratio(sum(e[tag]["top_is_primary"] for e in multi), len(multi)),
            "multi_all_found": ratio(sum(bool(e[tag]["all_found"]) for e in multi), len(multi)),
            "all_top_in_set": ratio(sum(e[tag]["top_in_set"] for e in det), len(det)),
        }

    gold_alerts = [a for a in alerts if not a["false_alarm"]]
    out: dict[str, Any] = {
        "scenarios": len(scores), "null_scenarios": len(null), "campaign_days": days,
        "detection": {
            "gold_episodes": len(eps), "detected": ratio(len(det), len(eps)), "delay_median": statistics.median(delays) if delays else None,
            "delay_p90": percentile([float(d) for d in delays], 0.9) if delays else None,
            "within3": ratio(sum(d <= 3 for d in delays), len(eps)), "within7": ratio(sum(d <= 7 for d in delays), len(eps)),
            "onset_error_median": statistics.median(e["onset_error"] for e in det) if det else None,
        },
        "false_alarms": {
            "episodes": sum(s["false_alarms"] for s in scores), "per_100_days": round(100 * sum(s["false_alarms"] for s in scores) / max(days, 1), 3),
            "null_scenarios_with_alert": ratio(sum(s["alerts"] > 0 for s in null), len(null)),
            "scenarios_with_false_alarm": ratio(sum(s["false_alarms"] > 0 for s in scores), len(scores)),
        },
        "diagnosis_alert3": diag("alert3"), "diagnosis_final": diag("final"),
        "explained_rate": ratio(sum(bool(e["alert3"]["explained"]) for e in det), len(det)),
        "actions": {
            "alerts": len(alerts), "harmful": ratio(sum(a["harmful"] for a in alerts), len(alerts)),
            "useful_on_true_alerts": ratio(sum(a["useful"] for a in gold_alerts), len(gold_alerts)),
            "scenarios_with_harmful_action": ratio(sum(any(a["harmful"] for a in s["per_alert"]) for s in scores), len(scores)),
        },
    }
    by: dict[str, dict[str, Any]] = {}
    groups: dict[str, Callable[[dict[str, Any]], str]] = {
        "noise": lambda s: s["noise"], "pattern": lambda s: s["pattern"], "history": lambda s: s["history"],
        "kind": lambda s: "none" if not s["incidents"] else ("subtle" if not s["gold_episodes"] else ("multi" if s["incidents"] > 1 else "single")),
        "magnitude": lambda s: _bucket(s["max_magnitude"]) if s["incidents"] else "none",
        "volume": lambda s: "small(<=100k)" if s["budget"] <= 100_000 else ("mid" if s["budget"] < 1_000_000 else "large(>=1M)"),
    }
    for gname, key in groups.items():
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for s in scores:
            buckets[key(s)].append(s)
        by[gname] = {}
        for b, sel in sorted(buckets.items()):
            ge = [e for s in sel for e in s["episodes"]]
            gd = [e for e in ge if e["detected"]]
            sa = [pa for s in sel for pa in s["per_alert"]]
            by[gname][b] = {
                "scenarios": len(sel), "gold_episodes": len(ge), "detected": ratio(len(gd), len(ge)),
                "top_in_set_alert3": ratio(sum(e["alert3"]["top_in_set"] for e in gd), len(gd)),
                "false_alarm_episodes": sum(s["false_alarms"] for s in sel), "harmful_alerts": ratio(sum(a["harmful"] for a in sa), len(sa)),
            }
    out["by"] = by
    by_cause: dict[str, dict[str, Any]] = {}
    cause_eps: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in eps:
        for c in e["causes"]:
            cause_eps[c].append(e)
    for c, lst in sorted(cause_eps.items()):
        d = [e for e in lst if e["detected"]]
        by_cause[c] = {"episodes": len(lst), "detected": ratio(len(d), len(lst)), "top_in_set_alert3": ratio(sum(e["alert3"]["top_in_set"] for e in d), len(d)),
                       "delay_median": statistics.median(e["delay"] for e in d) if d else None}
    out["by_cause"] = by_cause
    out["misses"] = [{"id": s["id"], "gold": e["gold"], "causes": e["causes"]} for s in scores for e in s["episodes"] if not e["detected"]]
    out["wrong_cause"] = [{"id": s["id"], "gold": e["gold"], "causes": e["causes"], "got": e["alert3"]["top"]} for s in scores for e in s["episodes"]
                          if e["detected"] and not e["alert3"]["top_in_set"]]
    out["harmful_scenarios"] = sorted({s["id"] for s in scores if any(a["harmful"] for a in s["per_alert"])})
    return out


def evaluate(scenarios: list[IncidentScenario], arms: tuple[str, ...] = ARMS) -> dict[str, Any]:
    sims = [(sc, simulate(sc)) for sc in scenarios]
    res: dict[str, Any] = {}
    for arm in arms:
        scores = [score_scenario(sc, sim, ARM_RUN[arm](sim)) for sc, sim in sims]
        res[arm] = aggregate(scores)
        res[arm]["per_scenario"] = [{k: s[k] for k in ("id", "alerts", "false_alarms", "gold_episodes", "causes", "primary")} for s in scores]
    return res


def counts(scenarios: list[IncidentScenario]) -> dict[str, Any]:
    sims = [simulate(sc) for sc in scenarios]
    gs = [gold(s) for s in sims]
    return {
        "scenarios": len(scenarios), "null": sum(g["null"] for g in gs), "multi": sum(len(sc.incidents) > 1 for sc in scenarios),
        "gold_episodes": sum(len(g["episodes"]) for g in gs), "causes": dict(Counter(c for g in gs for c in g["causes"])),
        "history": dict(Counter(_history_kind(sc, s.events, g) for sc, s, g in zip(scenarios, sims, gs, strict=True))),
    }


def power_curve(
    budgets: tuple[int, ...] = (100_000, 500_000, 2_000_000), mags: tuple[float, ...] = (0.3, 0.5, 0.9), causes: tuple[str, ...] = ("tracking_outage", "landing_degradation"),
    seeds: range = range(1, 10),
) -> dict[str, Any]:
    """전환이 적은 캠페인은 전환 측정 장애·랜딩 악화를 얼마나 알아채는가(개발용 시뮬레이션, 동결 셋과 무관). 일 예산 × 크기별 검출 비율·지연·원인 정확도."""
    out: dict[str, Any] = {}
    for cause in causes:
        out[cause] = {}
        for budget in budgets:
            for mag in mags:
                det = right = 0
                dl: list[int] = []
                for seed in seeds:
                    sc = IncidentScenario.model_validate({
                        "id": f"p-{cause}-{budget}-{mag}-{seed}", "seed": seed, "days": 42, "start_weekday": seed % 7,
                        "campaign": {"noise_level": ("low", "mid", "high")[seed % 3], "daily_budget_krw": budget},
                        "incidents": [{"cause": cause, "start_day": 20, "magnitude": mag}]})
                    sim = simulate(sc)
                    obs = Observation(sim.days, sim.events)
                    full = detect(obs)
                    hit = [e for e in full.episodes if e.alert_day >= 19]
                    if hit and hit[0].alert_day - 20 <= 14:
                        det += 1
                        dl.append(hit[0].alert_day - 20)
                        right += diagnose(obs, full, hit[0]).top == cause
                out[cause][f"b{budget}_m{round(mag * 100)}"] = {
                    "budget": budget, "magnitude": mag, "n": len(seeds), "detected": ratio(det, len(seeds)),
                    "delay_median": statistics.median(dl) if dl else None, "cause_ok": ratio(right, det) if det else None,
                    "conversions_per_day_approx": round(budget * 0.97 / 4500 * 1000 * 0.012 * 0.03, 1),
                }
    return out
