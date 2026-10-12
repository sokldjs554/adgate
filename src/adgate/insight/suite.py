"""성과 인사이트 측정 묶음(docs/EVAL_INSIGHT.md).

* ``insight`` — 결정론: 탐지·진단·액션을 팔 6개(시스템, 절제 3, 단순 규칙, 이력만)로 쟀다. 모델이 없어 ``offline`` 이 전부다.
* ``insight_llm`` — 모델이 들어가는 둘: ① 사실에 묶인 리포트 작성(시스템의 사실 → LLM 문단 → 코드 검증), ② 모델만 쓰는 비교 기준(일별 표를 주고 도구 없이 진단).
"""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from ..eval.metrics import ratio
from ..llm import CaseOutcome, LLMClient, LLMTask, ScriptedClient
from ..llm.core import Part
from ..suites import Driver, Suite, Variant, register
from .analysis import Observation, detect, diagnose, recommend
from .evaluate import (
    CHANGE_ACTIONS,
    SAFE_ACTIONS,
    counts,
    episode_causes,
    evaluate,
    load_scenarios,
    power_curve,
)
from .report import (
    build_facts,
    raw_input,
    raw_task,
    report_input,
    report_task,
    template_report,
    verify_report,
)
from .sim import IncidentScenario, gold, simulate

DATA = Path(__file__).resolve().parents[3] / "data" / "insight" / "scenarios.jsonl"
VARIANTS = {"sonnet": Variant("sonnet", "claude-sonnet-5-5", "리포트 작성 + 모델만 쓰는 비교 기준"), "opus": Variant("opus", "claude-opus-5-5", "모델만 쓰는 비교 기준(강한 모델)")}
MAX_EPISODES_PER_SCENARIO = 3


def load_frozen(path: Path | None = None) -> list[IncidentScenario]:
    return load_scenarios(path or DATA)


def _episodes_with_facts(sc: IncidentScenario) -> list[dict[str, Any]]:
    sim = simulate(sc)
    obs = Observation(sim.days, sim.events)
    det = detect(obs)
    out = []
    for ep in det.episodes[:MAX_EPISODES_PER_SCENARIO]:
        part = detect(obs, upto=min(len(sim.days), ep.alert_day + 3))
        match = next((e for e in part.episodes if e.alert_day == ep.alert_day), None)
        if match is None:
            continue
        dx = diagnose(obs, part, match)
        recs = recommend(obs, dx)
        out.append({"ep": match, "dx": dx, "recs": recs, "facts": build_facts(obs, part, match, dx, recs)})
    return out


def _writer_arm(sc: IncidentScenario, client: LLMClient) -> list[dict[str, Any]]:
    rows = []
    for item in _episodes_with_facts(sc):
        facts, dx, recs = item["facts"], item["dx"], item["recs"]
        tpl = verify_report(template_report(facts, dx, recs), facts, dx, recs)
        rep = client.call(report_task(), report_input(facts)).response
        problems = verify_report(rep, facts, dx, recs)
        steps = {s["action"] for s in rep.get("next_steps", [])}
        rows.append({
            "alert_day": item["ep"].alert_day, "template_problems": tpl, "problems": problems, "verified": not problems,
            "steps_subset_of_recs": steps <= ({r["action"] for r in recs} | set(SAFE_ACTIONS)), "n_paragraphs": len(rep.get("paragraphs", [])),
            "problem_kinds": sorted({p.split(":")[-1].strip()[:12] for p in problems}),
        })
    return rows


def _raw_arm(sc: IncidentScenario, client: LLMClient) -> dict[str, Any]:
    sim = simulate(sc)
    g = gold(sim)
    obs = Observation(sim.days, sim.events)
    out = client.call(raw_task(), raw_input(obs)).response
    causes = ({out["cause"], *out["other_causes"]} - {"none", "unknown"})
    first_ep = g["episodes"][0] if g["episodes"] else None
    gold_causes = set(episode_causes(sc, g, first_ep)) if first_ep else set()
    all_causes = {c for ep in g["episodes"] for c in episode_causes(sc, g, ep)}         # 시나리오의 모든 구간의 원인(분석용 — 채점은 첫 구간 기준이다)
    acts = set(out["actions"])
    harmful = bool(acts & (set(CHANGE_ACTIONS) if g["null"] else set(g["harmful_actions"])))
    return {
        "null": g["null"], "anomaly": bool(out["anomaly"]), "top_in_set": out["cause"] in gold_causes, "any_in_set": bool(causes & gold_causes),
        "primary_ok": out["cause"] == g["primary_cause"], "harmful": harmful, "useful": bool(acts & set(g["acceptable_actions"])) if not g["null"] else acts <= set(SAFE_ACTIONS),
        "start_error": abs(out["start_day"] - g["onset"]) if (out["start_day"] is not None and g["onset"] is not None) else None,
        # 실패 분석용(채점에는 쓰지 않는다): 모델이 낸 것과 정답
        "detail": {
            "model": {"cause": out["cause"], "other_causes": sorted(out["other_causes"]), "actions": sorted(acts), "start_day": out["start_day"]},
            "gold": {"primary": g["primary_cause"], "causes": sorted(gold_causes), "all_causes": sorted(all_causes), "harmful": sorted(set(CHANGE_ACTIONS) if g["null"] else g["harmful_actions"]), "onset": g["onset"]},
        },
    }


def _agg_raw(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [r for r in rows if not r["null"]]
    null = [r for r in rows if r["null"]]
    flagged = [r for r in pos if r["anomaly"]]
    errs = [r["start_error"] for r in flagged if r["start_error"] is not None]
    return {
        "scenarios": len(rows), "anomaly_recall": ratio(len(flagged), len(pos)), "null_false_alarm": ratio(sum(r["anomaly"] for r in null), len(null)),
        "top_in_set": ratio(sum(r["top_in_set"] for r in flagged), len(flagged)), "primary_ok": ratio(sum(r["primary_ok"] for r in flagged), len(flagged)),
        "harmful_actions": ratio(sum(r["harmful"] for r in rows), len(rows)), "useful_actions": ratio(sum(r["useful"] for r in rows), len(rows)),
        "start_error_median": statistics.median(errs) if errs else None,
    }


def _score(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
    ok = [o for o in outcomes if o.result is not None]
    res: dict[str, Any] = {"cases": len(outcomes), "failed": [o.case for o in outcomes if o.result is None]}
    writer = [r for o in ok for r in o.result.get("writer", [])]
    if writer:
        kinds = Counter(k for r in writer for k in r["problem_kinds"])
        res["writer"] = {
            "episodes": len(writer), "verified": ratio(sum(r["verified"] for r in writer), len(writer)),
            "steps_subset_of_recs": ratio(sum(r["steps_subset_of_recs"] for r in writer), len(writer)),
            "template_verified": ratio(sum(not r["template_problems"] for r in writer), len(writer)), "problem_kinds": dict(kinds),
        }
    raws = [o.result["raw"] for o in ok if "raw" in o.result]
    if raws:
        res["raw"] = _agg_raw(raws)
    return res


def _analyze(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
    """모델만 쓴 비교 기준(raw)이 *어느 시나리오에서* 틀렸는가와 시스템(결정론)이 놓친 구간과의 겹침. 집계 수치가 비슷해도 틀린 곳이 같다는 뜻은 아니다."""
    raws = {o.case: o.result["raw"] for o in outcomes if o.result is not None and "raw" in o.result}

    def pick(row: dict[str, Any]) -> dict[str, Any]:
        return {"model": row["detail"]["model"], "gold": row["detail"]["gold"]}

    missed = {i: r for i, r in raws.items() if not r["null"] and not r["anomaly"]}
    wrong_cause = {i: r for i, r in raws.items() if not r["null"] and r["anomaly"] and not r["top_in_set"]}
    harmful = {i: r for i, r in raws.items() if r["harmful"]}
    false_alarm = {i: r for i, r in raws.items() if r["null"] and r["anomaly"]}
    system_missed = {m["id"] for m in evaluate(load_frozen())["system"]["misses"]}
    flagged = {i: r for i, r in raws.items() if not r["null"] and r["anomaly"]}
    return {
        # 채점기(top_in_set)는 *첫 구간*의 원인만 정답으로 본다. 구간이 여럿인 시나리오에서는 시나리오의 다른 구간 원인을 말해도 틀린 것으로 센다 — 같은 비교를 시나리오 전체 기준으로도 적는다
        "raw_top_in_any_episode": ratio(sum(r["detail"]["model"]["cause"] in r["detail"]["gold"]["all_causes"] for r in flagged.values()), len(flagged)),
        "raw_wrong_cause_but_scenario_primary": sorted(i for i, r in wrong_cause.items() if r["detail"]["model"]["cause"] == r["detail"]["gold"]["primary"]),
        "raw_missed": {i: pick(r) for i, r in sorted(missed.items())},
        "raw_wrong_cause": {i: pick(r) for i, r in sorted(wrong_cause.items())},
        "raw_harmful": {i: pick(r) for i, r in sorted(harmful.items())},
        "raw_false_alarms": {i: pick(r) for i, r in sorted(false_alarm.items())},
        "system_missed_scenarios": sorted(system_missed),
        "both_missed": sorted(system_missed & set(missed)),
        "raw_only_missed": sorted(set(missed) - system_missed),
        "system_only_missed": sorted((system_missed & set(raws)) - set(missed)),
        "writer_unverified": [o.case for o in outcomes if o.result is not None and any(not r["verified"] for r in o.result.get("writer", []))],
    }


def _standin(payload: dict[str, Any]) -> ScriptedClient:
    sc: IncidentScenario = payload["sc"]
    items = {i["ep"].alert_day: i for i in _episodes_with_facts(sc)}

    def handler(task: LLMTask, parts: list[Part]) -> dict[str, Any]:
        if task.name == "insight.report":
            import json

            facts = json.loads(str(parts[0]))["facts"]
            ids = [f["id"] for f in facts]
            it = next(iter(items.values()))
            return {"headline": "점검", "paragraphs": [{"text": template_report(it["facts"], it["dx"], it["recs"])["paragraphs"][0]["text"], "fact_ids": ids[:1]}], "next_steps": []}
        g = gold(simulate(sc))
        return {"anomaly": not g["null"], "start_day": g["onset"], "cause": g["primary_cause"] or "none", "other_causes": [], "actions": [(g["acceptable_actions"] or ["no_action"])[0]], "report": "점검"}

    return ScriptedClient(handler)


@register("insight_llm")
def insight_llm() -> Suite:
    scenarios = load_frozen()

    def cases(variant: str) -> list[tuple[str, Any]]:
        arms = ("writer", "raw") if variant == "sonnet" else ("raw",)
        return [(sc.id, {"sc": sc, "arms": arms}) for sc in scenarios]

    def driver(variant: str) -> Driver:
        def run(payload: dict[str, Any], client: LLMClient) -> dict[str, Any]:
            sc: IncidentScenario = payload["sc"]
            out: dict[str, Any] = {}
            if "writer" in payload["arms"]:
                out["writer"] = _writer_arm(sc, client)
            if "raw" in payload["arms"]:
                out["raw"] = _raw_arm(sc, client)
            return out

        return run

    return Suite("insight_llm", "성과 인사이트: 사실에 묶인 리포트 작성 + 모델만 쓰는 비교 기준", "docs/EVAL_INSIGHT.md", VARIANTS, cases, driver, _standin, _score, analyze=_analyze)


@register("insight")
def insight() -> Suite:
    """모델이 없는 결정론 평가. ``adgate llm offline insight`` 가 전부다."""

    def offline() -> dict[str, Any]:
        scs = load_frozen()
        return {"counts": counts(scs), "arms": evaluate(scs)}

    return Suite("insight", "성과 인사이트: 결정론 탐지·진단·액션(팔 6개)", "docs/EVAL_INSIGHT.md", {}, lambda v: [], lambda v: (lambda p, c: None), lambda p: ScriptedClient(lambda t, p2: {}),
                 lambda v, o: {}, offline=offline)


@register("insight_power")
def insight_power() -> Suite:
    """검출력 곡선: 일 예산(=전환 수) × 크기별로 전환 측정 장애·랜딩 악화를 얼마나 알아채는가. 개발용 시뮬레이션이며 동결 셋과 무관하다."""
    return Suite("insight_power", "성과 인사이트: 전환 수에 따른 검출력 곡선(개발용 시뮬레이션)", "docs/EVAL_INSIGHT.md", {}, lambda v: [], lambda v: (lambda p, c: None),
                 lambda p: ScriptedClient(lambda t, p2: {}), lambda v, o: {}, offline=lambda: power_curve())
