"""성과 시뮬레이터와 탐지·진단·액션. 시드가 정해져 있어 결정론이다.

탐지기는 *개발용* 시나리오(devset)로 조정했고, 독립 작성자가 쓴 동결 셋의 정답은 보지 않았다 — 여기 테스트는 그 동결 셋이 아니라 성질(인과성·정직한 정지·해로운 액션 회피)을 본다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adgate.insight.analysis import (
    Observation,
    _freq_step,
    _ols,
    detect,
    diagnose,
    recommend,
    ref_windows,
)
from adgate.insight.sim import ACCEPTABLE, HARMFUL, IncidentScenario, anomaly_days, gold, simulate


def make(cause: str | None, mag: float = 0.5, *, seed: int = 1, noise: str = "mid", budget: int = 500_000, start: int = 20, days: int = 42,
         extra: dict | None = None, distractors: list[dict] | None = None, **campaign: object) -> IncidentScenario:
    incidents = [] if cause is None else [{"cause": cause, "start_day": start, "magnitude": mag, **(extra or {})}]
    return IncidentScenario.model_validate({
        "id": f"t-{cause}-{seed}", "seed": seed, "days": days, "start_weekday": seed % 7,
        "campaign": {"noise_level": noise, "daily_budget_krw": budget, **campaign}, "incidents": incidents, "distractor_events": distractors or []})


def run(sc: IncidentScenario):
    sim = simulate(sc)
    obs = Observation(sim.days, sim.events)
    return sim, obs, gold(sim), detect(obs)


# ------------------------------------------------------------------ 시뮬레이터
def test_simulation_is_deterministic():
    a, b = simulate(make("budget_cut")), simulate(make("budget_cut"))
    assert [d.impressions for d in a.days] == [d.impressions for d in b.days]
    assert simulate(make("budget_cut", seed=2)).days[3].impressions != a.days[3].impressions


def test_no_incident_has_no_anomaly_days():
    sim = simulate(make(None))
    assert anomaly_days(sim) == []
    assert gold(sim)["null"] is True


@pytest.mark.parametrize("cause,metric,sign", [("budget_cut", "spend", -1), ("seasonal_cpm_spike", "impressions", -1), ("tracking_outage", "conversions", -1)])
def test_incident_moves_the_metric_it_should(cause, metric, sign):
    extra = {"end_day": 26} if cause == "seasonal_cpm_spike" else None
    sim = simulate(make(cause, 0.5, extra=extra))
    day = 21
    w, wo = sim.expected_with[day].metric(metric), sim.expected_without[day].metric(metric)
    assert (w - wo) * sign > 0


def test_tracking_outage_leaves_clicks_and_bounce_alone_but_landing_raises_bounce():
    t = simulate(make("tracking_outage", 0.6))
    assert t.expected_with[25].clicks == pytest.approx(t.expected_without[25].clicks)
    assert t.expected_with[25].bounce_rate == pytest.approx(t.expected_without[25].bounce_rate)
    la = simulate(make("landing_degradation", 0.6))
    assert la.expected_with[25].bounce_rate > la.expected_without[25].bounce_rate + 0.1


def test_ad_rejected_zeroes_target_creative_but_account_barely_moves():
    sim = simulate(make("ad_rejected", 1.0, extra={"creative": 2}))
    assert sim.expected_with[25].creatives[2]["impressions"] == 0
    assert sim.expected_with[25].impressions == pytest.approx(sim.expected_without[25].impressions)
    assert gold(sim)["anomaly_days"], "소재별 지표는 정답에 들어간다"


def test_scenario_validation_rejects_bad_input():
    with pytest.raises(ValidationError):
        make("creative_fatigue")                                  # 대상 소재 없음
    with pytest.raises(ValidationError):
        make("budget_cut", extra={"creative": 1})                 # 필요 없는 소재
    with pytest.raises(ValidationError):
        make("ad_rejected", 0.5, extra={"creative": 0})           # 반려는 magnitude 1.0
    with pytest.raises(ValidationError):
        make("budget_cut", start=5)                               # 기준선 7일 안에는 사건 없음


def test_gold_actions_follow_the_table():
    g = gold(simulate(make("bid_drop", 0.4)))
    assert g["acceptable_actions"] == sorted(ACCEPTABLE["bid_drop"])
    assert set(g["harmful_actions"]) == set(HARMFUL["bid_drop"])


# ------------------------------------------------------------------ 기준 창
def test_reference_windows_skip_flagged_days_and_align_weekdays():
    flagged = [False] * 30
    assert ref_windows(flagged, 20, 3) == [(11, 13), (4, 6)]      # 1·2주 전 같은 요일
    flagged[12] = True
    assert ref_windows(flagged, 20, 3) == [(4, 6)]
    long = ref_windows([False] * 40, 30, 7)
    assert long[0] == (17, 23) and all(b - a == 6 for a, b in long)
    assert ref_windows([False] * 5, 4, 3) == []


def test_ols_and_frequency_step_recover_trend_and_step():
    rows = [[1.0, float(t)] for t in range(10)]
    beta, _inv, rss = _ols(rows, [2.0 + 0.5 * t for t in range(10)])  # type: ignore[misc]
    assert beta == pytest.approx([2.0, 0.5]) and rss == pytest.approx(0.0, abs=1e-9)
    freq = [1.3 + 0.012 * t + (0.3 if t >= 20 else 0.0) for t in range(36)]
    step = _freq_step(freq, 19, 35)
    assert step is not None and step.value == pytest.approx(0.3, abs=0.03)
    assert _freq_step(freq[:8], 7, 7) is None


# ------------------------------------------------------------------ 탐지
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_quiet_campaign_stays_quiet_at_low_and_mid_noise(seed):
    for noise in ("low", "mid"):
        _, _, g, det = run(make(None, seed=seed, noise=noise, budget=200_000 * seed))
        assert g["null"] and det.episodes == [], f"seed={seed} noise={noise}"


@pytest.mark.parametrize("cause,mag,extra", [
    ("budget_cut", 0.5, None), ("bid_drop", 0.4, None), ("audience_saturation", 0.4, None), ("tracking_outage", 0.7, None),
    ("landing_degradation", 0.7, None), ("creative_fatigue", 0.6, {"creative": 1}), ("ad_rejected", 1.0, {"creative": 0}),
])
def test_obvious_incidents_are_found_within_a_week_and_diagnosed(cause, mag, extra):
    sim, obs, g, det = run(make(cause, mag, seed=3, extra=extra))
    first_gold = g["episodes"][0][0]
    covering = [e for e in det.episodes if e.alert_day >= first_gold - 1]
    assert covering, "경보가 없다"
    assert covering[0].alert_day - first_gold <= 7
    assert diagnose(obs, det, covering[0]).top == cause


def test_detection_is_causal():
    """같은 날까지의 관측만 쓰므로, 앞부분만 잘라서 돌려도 그 구간의 경보일이 같다."""
    sim, obs, _, full = run(make("budget_cut", 0.4, seed=4))
    for cut in (24, 30, 38):
        part = detect(obs, upto=cut)
        assert part.alert_days == [d for d in full.alert_days if d < cut]


def test_misleading_history_does_not_create_a_cause():
    """입찰이 떨어졌는데 같은 시점에 '일 예산 변경' 이력도 있다 — 진단은 데이터 서명(예산은 그대로, 소진율만 하락)을 따른다."""
    for seed in (5, 6, 7):
        sc = make("bid_drop", 0.4, seed=seed, distractors=[{"day": 19, "type": "budget_change", "detail": "일 예산 500,000원 → 450,000원"}])
        sim, obs, _, det = run(sc)
        assert det.episodes
        dx = diagnose(obs, det, det.episodes[0])
        assert dx.top == "bid_drop", dx.candidates[:2]
        assert "raise_budget" not in {r["action"] for r in recommend(obs, dx)}


def test_history_alone_cannot_raise_an_alarm():
    """이력에 예산 삭감이 적혀 있어도 지표가 흔들리지 않으면 경보가 없다."""
    sc = make(None, seed=3, noise="low", distractors=[{"day": 21, "type": "budget_change", "detail": "일 예산 500,000원 → 250,000원"},
                                                     {"day": 22, "type": "ad_rejected", "detail": "소재 1 심사 반려"}])
    _, _, g, det = run(sc)
    assert g["null"] and det.episodes == []


# ------------------------------------------------------------------ 액션
@pytest.mark.parametrize("cause,mag,extra", [
    ("budget_cut", 0.5, None), ("bid_drop", 0.4, None), ("audience_saturation", 0.4, None), ("tracking_outage", 0.7, None),
    ("landing_degradation", 0.7, None), ("creative_fatigue", 0.6, {"creative": 1}), ("ad_rejected", 1.0, {"creative": 0}),
    ("seasonal_cpm_spike", 0.6, {"end_day": 25}),
])
def test_recommended_actions_are_never_harmful(cause, mag, extra):
    for seed in (1, 2, 3):
        sim, obs, g, det = run(make(cause, mag, seed=seed, extra=extra))
        for e in det.episodes:
            acts = [r["action"] for r in recommend(obs, diagnose(obs, det, e))]
            assert not set(acts) & set(g["harmful_actions"]), (cause, seed, acts)


def test_unexplained_alert_recommends_waiting_not_changing():
    sim, obs, _, det = run(make(None, seed=2))
    from adgate.insight.analysis import Diagnosis, Episode
    dx = Diagnosis(Episode(10, 12, 12), [], {}, {}, False)
    recs = recommend(obs, dx)
    assert [r["action"] for r in recs] == ["wait_and_monitor"] and recs[0]["cause"] is None


def test_overlapping_incidents_are_both_named_and_both_fixes_offered():
    sc = IncidentScenario.model_validate({
        "id": "multi", "seed": 1, "days": 48, "start_weekday": 1, "campaign": {"noise_level": "low", "daily_budget_krw": 800_000},
        "incidents": [{"cause": "budget_cut", "start_day": 20, "magnitude": 0.4, "primary": True}, {"cause": "tracking_outage", "start_day": 24, "magnitude": 0.7}]})
    sim, obs, g, det = run(sc)
    dx = diagnose(obs, det, det.episodes[0])
    assert {dx.top, dx.second.cause if dx.second else None} == {"budget_cut", "tracking_outage"}
    acts = {r["action"] for r in recommend(obs, dx)}
    assert acts <= set(g["acceptable_actions"]) | {"wait_and_monitor"} and not acts & set(g["harmful_actions"])


def test_small_campaign_cannot_see_a_partial_tracking_loss_but_does_not_cry_wolf():
    """전환이 하루 8건인 캠페인에서 전환 측정이 절반만 끊기면 3주 안에는 통계적으로 구별되지 않는다 — 경보가 없는 것이 정직한 결과다."""
    sim, obs, g, det = run(make("tracking_outage", 0.4, seed=1, budget=100_000, noise="mid"))
    assert not g["null"]
    assert det.episodes == []


# ------------------------------------------------------------------ 평가 하니스·리포트·동결 셋
from adgate.eval import dataset  # noqa: E402
from adgate.insight.devset import dev_scenarios  # noqa: E402
from adgate.insight.evaluate import ARM_RUN, aggregate, counts, run_history_only, run_naive, score_scenario  # noqa: E402
from adgate.insight.report import Fact, build_facts, raw_input, template_report, verify_report  # noqa: E402
from adgate.insight.suite import load_frozen  # noqa: E402


def test_frozen_insight_scenarios_are_the_registered_sixty_and_in_the_manifest():
    scs = load_frozen()
    assert len(scs) == 60 and len({s.id for s in scs}) == 60 and len({s.seed for s in scs}) == 60
    assert dataset.verify_manifest(ROOT_DIR / "data/MANIFEST.sha256")["insight/scenarios.jsonl"] is True
    c = counts(scs)
    assert c["null"] == 20 and c["multi"] == 16 and c["gold_episodes"] == 43
    assert set(c["causes"]) == {"budget_cut", "bid_drop", "creative_fatigue", "audience_saturation", "tracking_outage", "landing_degradation", "ad_rejected", "seasonal_cpm_spike"}


from pathlib import Path  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parent.parent


def test_every_arm_runs_and_scoring_matches_the_definitions_on_a_small_dev_sample():
    scs = [s for s in dev_scenarios() if s.id.startswith(("dev-budget_cut-0.5-1", "dev-null-100000-mid-flat-0", "dev-tracking_outage-0.9-2"))]
    assert len(scs) == 3
    sims = [(s, simulate(s)) for s in scs]
    for arm, fn in ARM_RUN.items():
        scores = [score_scenario(s, sim, fn(sim)) for s, sim in sims]
        agg = aggregate(scores)
        assert agg["scenarios"] == 3 and agg["detection"]["gold_episodes"] == 2 and agg["null_scenarios"] == 1, arm
    system = aggregate([score_scenario(s, sim, ARM_RUN["system"](sim)) for s, sim in sims])
    assert system["detection"]["detected"]["k"] == 2 and system["false_alarms"]["episodes"] == 0 and system["actions"]["harmful"]["k"] == 0


def test_history_only_baseline_is_fooled_by_a_misleading_log_and_blind_to_silent_failures():
    """조용한 전환 측정 장애인데 하루 전에 '일 예산' 이력만 있다 — 이력만 보는 방식은 원인을 예산 삭감으로 읽고 예산을 올리라고 한다(전환 측정 장애에는 해롭다)."""
    sc = make("tracking_outage", 0.9, seed=2, distractors=[{"day": 19, "type": "budget_change", "detail": "일 예산 점검(변경 없음)"}])
    sim = simulate(sc)
    scored = score_scenario(sc, sim, run_history_only(sim))
    assert scored["alerts"] == 1 and scored["episodes"][0]["detected"] and not scored["episodes"][0]["alert3"]["top_in_set"]
    assert scored["per_alert"][0]["harmful"] is True
    quiet = make("tracking_outage", 0.9, seed=2)
    assert run_history_only(simulate(quiet)) == [], "이력이 없으면 아무것도 못 본다"


def test_naive_dashboard_rule_cries_wolf_on_a_quiet_high_noise_campaign():
    sim = simulate(make(None, seed=3, noise="high", budget=100_000))
    assert len(run_naive(sim)) >= 1


def test_template_report_always_passes_verification_and_a_fabricated_one_does_not():
    sim = simulate(make("tracking_outage", 0.8, seed=3, budget=2_000_000))
    obs = Observation(sim.days, sim.events)
    det = detect(obs)
    ep = det.episodes[0]
    dx = diagnose(obs, det, ep)
    recs = recommend(obs, dx)
    facts = build_facts(obs, det, ep, dx, recs)
    assert any(f.kind == "cause" for f in facts) and all(f.id.startswith("F") for f in facts)
    rep = template_report(facts, dx, recs)
    assert verify_report(rep, facts, dx, recs) == []
    bad = {"headline": "예산 삭감입니다", "paragraphs": [{"text": "전환율이 -99% 떨어졌고 예산 삭감이 원인입니다.", "fact_ids": []}, {"text": "근거 없음", "fact_ids": ["F99"]}],
           "next_steps": [{"action": "raise_budget", "rationale": "x", "fact_ids": []}]}
    problems = verify_report(bad, facts, dx, recs)
    assert any("사실 인용 없음" in p for p in problems) and any("없는 사실" in p for p in problems) and any("금지 액션" in p for p in problems)
    assert any("진단 후보에 없는 원인" in p for p in problems)


def test_report_numbers_must_come_from_the_cited_facts():
    facts = [Fact("F1", "metric", "전환율 이(가) 평소 대비 -55%.", ["-55%"]), Fact("F2", "episode", "경보는 22일차.", ["22일"])]
    from adgate.insight.analysis import Diagnosis, Episode

    dx = Diagnosis(Episode(20, 30, 22), [], {}, {}, False)
    ok = {"headline": "h", "paragraphs": [{"text": "전환율이 55% 줄었고 22일차에 경보가 울렸습니다.", "fact_ids": ["F1", "F2"]}], "next_steps": []}
    assert verify_report(ok, facts, dx, []) == []
    wrong = {"headline": "h", "paragraphs": [{"text": "전환율이 70% 줄었습니다.", "fact_ids": ["F1"]}], "next_steps": []}
    assert any("70%" in p for p in verify_report(wrong, facts, dx, []))
    uncited = {"headline": "h", "paragraphs": [{"text": "22일차에 경보가 울렸습니다.", "fact_ids": ["F1"]}], "next_steps": []}
    assert any("22일차" in p for p in verify_report(uncited, facts, dx, [])), "인용한 사실(F1)에 없는 숫자는 다른 사실(F2)에 있어도 안 된다"


def test_raw_input_contains_only_observations_not_the_answer():
    sim = simulate(make("budget_cut", 0.5, seed=2))
    text = raw_input(Observation(sim.days, sim.events))
    assert "budget_cut" not in text and "expected" not in text and '"day": 1' in text


def test_insight_llm_failure_analysis_names_the_scenarios_where_the_model_only_baseline_went_wrong():
    """집계 수치가 비슷해도 틀린 곳이 같다는 뜻은 아니다 — 모델만 쓴 기준이 놓친 이상 · 오경보 · 해로운 액션을 시나리오 단위로 적는다."""
    from adgate.insight.suite import _analyze, _raw_arm, load_frozen
    from adgate.llm import CaseOutcome, ScriptedClient

    scs = load_frozen()
    null_sc = next(s for s in scs if gold(simulate(s))["null"])
    pos_sc = next(s for s in scs if not gold(simulate(s))["null"])

    def client(out):
        return ScriptedClient(lambda task, parts: out)

    cry_wolf = {"anomaly": True, "start_day": 10, "cause": "budget_cut", "other_causes": [], "actions": ["raise_budget"], "report": "x"}
    sleep = {"anomaly": False, "start_day": None, "cause": "none", "other_causes": [], "actions": ["no_action"], "report": "x"}
    outcomes = [
        CaseOutcome(null_sc.id, {"raw": _raw_arm(null_sc, client(cry_wolf))}),
        CaseOutcome(pos_sc.id, {"raw": _raw_arm(pos_sc, client(sleep))}),
    ]
    a = _analyze("opus", outcomes)
    assert list(a["raw_false_alarms"]) == [null_sc.id] and list(a["raw_harmful"]) == [null_sc.id]      # 사건이 없는데 예산을 올리자고 했다
    assert list(a["raw_missed"]) == [pos_sc.id]
    assert a["raw_missed"][pos_sc.id]["model"]["cause"] == "none" and a["raw_missed"][pos_sc.id]["gold"]["primary"]   # 모델이 낸 것과 정답이 같이 남는다
    assert set(a["both_missed"]) | set(a["raw_only_missed"]) == {pos_sc.id}
    assert a["writer_unverified"] == []
    assert set(a["raw_top_in_any_episode"]) == {"k", "n", "value", "ci"} and a["raw_top_in_any_episode"]["n"] == 0   # 이상을 잡았다고 한 시나리오가 없다


def test_a_cause_from_a_later_episode_is_wrong_for_the_scorer_but_right_for_the_scenario():
    """채점기는 첫 구간의 원인만 정답으로 본다. 구간이 여럿이면 시나리오의 다른 구간 원인을 말한 모델이 틀린 것으로 세어진다 — 분석이 그 차이를 따로 적는다."""
    from adgate.insight.evaluate import episode_causes
    from adgate.insight.suite import _analyze, _raw_arm, load_frozen
    from adgate.llm import CaseOutcome, ScriptedClient

    for sc in load_frozen():
        g = gold(simulate(sc))
        causes = [set(episode_causes(sc, g, e)) for e in g["episodes"]]
        later_only = (set().union(*causes[1:]) - causes[0]) if len(causes) > 1 else set()
        if later_only:
            reply = {"anomaly": True, "start_day": g["onset"], "cause": sorted(later_only)[0], "other_causes": [], "actions": ["wait_and_monitor"], "report": "x"}
            row = _raw_arm(sc, ScriptedClient(lambda task, parts, reply=reply: reply))
            assert row["top_in_set"] is False                                       # 채점기: 첫 구간 원인이 아니므로 틀림
            a = _analyze("opus", [CaseOutcome(sc.id, {"raw": row})])
            assert a["raw_top_in_any_episode"]["k"] == 1                            # 시나리오 전체로는 맞는 원인
            return
    pytest.skip("구간이 여럿이고 원인이 다른 동결 시나리오가 없다")


def test_the_failure_analysis_runs_on_a_partial_recording_without_inventing_cases():
    from adgate.insight.suite import _analyze

    assert _analyze("opus", [])["raw_missed"] == {} and _analyze("opus", [])["system_only_missed"] == []
