"""운영 리허설(ops/): 트래픽 생성, 드리프트 모니터, 섀도 게이트, 번레이트 경보, 부하·격리. 수치는 단정하지 않고 구조·불변식·악용/정상 유사 쌍을 검사한다."""

from __future__ import annotations

import collections

import pytest

from adgate.config import ReviewConfig
from adgate.eval import dataset
from adgate.ops import drift, shadow, slo
from adgate.ops.traffic import FINANCE, DriftSpec, day_factor, make_stream, subset

POOL = [e for n in ("dev", "test", "redteam", "gray") for e in dataset.load(f"data/{n}.jsonl")]


# --- 트래픽 -------------------------------------------------------------------------------------------------------------------------


def test_stream_is_deterministic_and_time_ordered() -> None:
    a = make_stream(POOL, days=10, per_day=100, seed=3)
    b = make_stream(POOL, days=10, per_day=100, seed=3)
    assert [(r.t, r.example.id, r.tenant) for r in a] == [(r.t, r.example.id, r.tenant) for r in b]
    assert [r.t for r in a] == sorted(r.t for r in a) and all(0 <= r.day < 10 for r in a)
    assert [r.example.id for r in make_stream(POOL, days=10, per_day=100, seed=4)] != [r.example.id for r in a]


def test_weekend_volume_is_lower_and_mean_volume_matches_the_rate() -> None:
    s = make_stream(POOL, days=70, per_day=400, seed=1)
    per_day = collections.Counter(r.day for r in s)
    wk = sum(per_day[d] for d in range(70) if d % 7 < 5) / 50
    we = sum(per_day[d] for d in range(70) if d % 7 >= 5) / 20
    assert we < 0.8 * wk and abs(len(s) / 70 - 400) < 40
    assert abs(sum(day_factor(d) for d in range(7)) / 7 - 1.0) < 1e-9


@pytest.mark.parametrize("kind", ["category_shift", "attack_wave", "obfuscation_surge"])
def test_drift_only_starts_after_start_day_and_draws_from_the_shifted_mix(kind: str) -> None:
    spec = DriftSpec(kind, 5, 0.5)  # type: ignore[arg-type]
    s = make_stream(POOL, days=10, per_day=500, seed=2, drift=spec)
    assert not any(r.drifted for r in s if r.day < 5)
    moved = [r for r in s if r.drifted]
    assert moved and {e.id for e in subset(POOL, spec.kind)} >= {r.example.id for r in moved}
    late = [r for r in s if r.day >= 5]
    assert 0.4 < sum(r.drifted for r in late) / len(late) < 0.6


def test_empty_shift_mix_is_an_error_not_a_silent_no_op() -> None:
    only_approve = [e for e in POOL if e.gold.decision == "APPROVE"]
    with pytest.raises(ValueError):
        make_stream(only_approve, days=3, per_day=10, seed=0, drift=DriftSpec("attack_wave", 1, 0.5))


# --- 드리프트 모니터 ---------------------------------------------------------------------------------------------------------------------


def _day(i: int, n: int, reject: int, **extra: int) -> drift.Day:
    d = drift.Day(i, n)
    d.x["reject_rate"] = reject
    for k, v in extra.items():
        d.x[k] = v
    d.sums["findings_per_item"], d.sqs["findings_per_item"] = n * 1.0, n * 1.5
    d.sums["headline_len"], d.sqs["headline_len"] = n * 20.0, n * 410.0
    return d


def test_sustained_shift_alarms_after_the_persistence_window_and_a_single_spike_does_not() -> None:
    base = [_day(i, 500, 150) for i in range(7)]
    shifted = [*base, *[_day(7 + i, 500, 260) for i in range(5)]]
    r = drift.detect(shifted)
    assert r.alarm_day == 8 and r.signal == "reject_rate"                      # 7일차에 처음 넘고 8일차에 연속 두 번째
    spike = [*base, _day(7, 500, 260), *[_day(8 + i, 500, 150) for i in range(5)]]
    assert drift.detect(spike).alarm_day is None                               # 하루짜리 튐은 체계 모니터가 무시한다
    assert drift.detect(spike, drift.NAIVE).alarm_day == 7                     # 순진한 모니터는 울린다(오경보 후보)


def test_small_days_are_skipped_and_tiny_reference_is_refused() -> None:
    with pytest.raises(ValueError):
        drift.detect([_day(i, 3, 1) for i in range(10)])
    base = [_day(i, 500, 150) for i in range(7)]
    r = drift.detect([*base, _day(7, 5, 5), _day(8, 5, 5)])
    assert r.alarm_day is None and r.max_z_by_day == [0.0, 0.0]                # 표본이 너무 적은 날은 판단하지 않는다


def test_zero_variance_does_not_crash() -> None:
    base = [_day(i, 100, 0) for i in range(7)]
    assert drift.detect([*base, _day(7, 100, 0), _day(8, 100, 0)]).alarm_day is None
    assert drift.detect([*base, _day(7, 100, 50), _day(8, 100, 50)]).alarm_day == 8


def test_observation_is_label_free_and_cached_per_example() -> None:
    obs = drift.observe(POOL[:20])
    assert set(obs) == {e.id for e in POOL[:20]} and all(isinstance(o.findings, int) for o in obs.values())
    risky_finance = [e for e in POOL[:60] if e.creative.advertiser.category in FINANCE]
    assert all(obs[e.id].finance for e in risky_finance if e.id in obs)


def test_monitor_catches_a_real_attack_wave_and_is_quiet_on_a_null_stream() -> None:
    obs = drift.observe(POOL)
    wave = drift.summarize(make_stream(POOL, days=30, per_day=800, seed=5, drift=DriftSpec("attack_wave", 14, 0.5)), obs, 30)
    r = drift.detect(wave)
    assert r.alarm_day is not None and 14 <= r.alarm_day <= 16
    quiet = [drift.detect(drift.summarize(make_stream(POOL, days=30, per_day=800, seed=100 + s), obs, 30)).alarm_day for s in range(6)]
    assert quiet.count(None) >= 5                                              # 사건 없는 스트림 6개 중 오경보는 많아야 1개(느슨한 확인; 정확한 비율은 사전등록 측정이 낸다)


# --- 섀도 게이트 --------------------------------------------------------------------------------------------------------------------


def test_compare_counts_directions() -> None:
    stream = POOL[:60]
    prod, cand = shadow.engine_of(ReviewConfig()), shadow.engine_of(ReviewConfig().without("exceptions"))
    rep = shadow.compare(stream, prod, cand)
    assert rep.disagree == rep.to_safer + rep.to_looser == sum(rep.transitions.values()) and rep.n == 60
    same = shadow.compare(stream, prod, prod)
    assert same.disagree == 0 and same.transitions == {}


def test_gate_allows_identical_blocks_degraded_and_allows_improved() -> None:
    stream = POOL[:200]
    good, no_norm = shadow.engine_of(ReviewConfig()), shadow.engine_of(ReviewConfig().without("normalize"))
    assert shadow.promote_gate(stream, good, good).allow and shadow.promote_gate(stream, good, good).reason == "동일"
    bad = shadow.promote_gate(stream, good, no_norm)
    assert not bad.allow and bad.spot["cand_cost"] > bad.spot["prod_cost"]
    better = shadow.promote_gate(stream, no_norm, good)
    assert better.allow and better.spot["cand_cost"] <= better.spot["prod_cost"]


def test_exact_loss_cannot_see_the_safety_direction_but_the_safety_loss_can() -> None:
    """F-24: 정답 REJECT 인 소재를 운영은 REVIEW(안전), 후보는 APPROVE(위험)로 판정한다. 둘 다 정답과 다르므로 정확도 손실로는 '똑같이 틀림'이다."""
    from adgate.eval.dataset import Example, Gold

    items = [Example(id=f"X{i}", creative=POOL[0].creative, gold=Gold(decision="REJECT", violations=[], tags=[])) for i in range(30)]
    prod, cand = (lambda e: "REVIEW"), (lambda e: "APPROVE")
    exact = shadow.promote_gate(items, prod, cand, shadow.GateRule(loss="exact", max_disagreement=1.0))
    safe = shadow.promote_gate(items, prod, cand, shadow.GateRule(loss="safety", max_disagreement=1.0))
    assert exact.allow and exact.spot["prod_wrong"] == exact.spot["cand_wrong"]                   # 정확도 손실: 통과(위험한 후보를 놓친다)
    assert not safe.allow and safe.spot["cand_cost"] > safe.spot["prod_cost"]                      # 안전 손실: 막는다
    assert shadow.promote_gate(items, cand, prod, shadow.GateRule(loss="safety", max_disagreement=1.0)).allow   # 반대 방향(더 안전하게 바뀜)은 통과
    assert shadow.cost("APPROVE", "REJECT", "safety") > shadow.cost("APPROVE", "REVIEW", "safety") and shadow.cost("REJECT", "REJECT", "safety") == 0


def test_exact_loss_reproduces_the_preserved_first_contact_rows() -> None:
    """사전등록한 첫 접촉은 정확도 손실(exact)이었다. 같은 규칙으로 다시 돌리면 보존된 산출물과 같아야 한다 — 손실을 바꾸면서 첫 접촉을 못 재현하는 일이 없게."""
    import json
    from pathlib import Path

    kept = json.loads((Path(__file__).resolve().parents[1] / "artifacts/history/ops_first_contact.json").read_text(encoding="utf-8"))["results"]["shadow"]["rows"]
    now = shadow.evaluate_shadow(POOL, rule=shadow.GateRule(loss="exact"))["rows"]
    # `disagree_mean`(운영과 후보의 판정이 갈린 소재 수)은 손실이 아니라 *엔진*이 정한다. 이미지 셋 수정(F-31) 뒤 회색지대 G-E09·G-G11 이 새로 REJECT 가 돼서
    # 정규화를 끈 후보(no_normalize, 'APK)을 설치' 의 괄호를 못 걷는다)와의 불일치가 5.45 → 6.1 이 됐다. 손실이 정하는 것(통과·차단·정답 수)만 첫 접촉과 견준다.
    assert [(r["candidate"], r["allowed"], r["blocked"], r["right"]) for r in now] == [(r["candidate"], r["allowed"], r["blocked"], r["right"]) for r in kept]


def test_gate_refuses_when_too_many_decisions_flip_without_a_spot_check() -> None:
    stream = POOL[:150]
    blind = shadow.engine_of(ReviewConfig())
    flipper = lambda e: "REJECT" if e.id.endswith(("0", "1", "2")) else blind(e)  # noqa: E731 — 열화가 아주 큰 후보
    d = shadow.promote_gate(stream, blind, flipper, shadow.GateRule(max_disagreement=0.05))
    assert not d.allow and "달라졌다" in d.reason and d.spot == {}


def test_reviewer_noise_can_flip_spot_check_outcomes_but_not_the_identical_case() -> None:
    stream = POOL[:100]
    good = shadow.engine_of(ReviewConfig())
    noisy = shadow.GateRule(reviewer_error=0.3)
    assert shadow.promote_gate(stream, good, good, noisy).allow           # 같은 엔진은 검토자 오류와 무관하게 통과(달라진 소재가 없다)


# --- SLO·번레이트 ------------------------------------------------------------------------------------------------------------------


def test_budget_and_burn_rate_arithmetic() -> None:
    s = slo.SLO("x", 0.999)
    assert abs(s.budget - 0.001) < 1e-12
    n = [1000] * 120
    e = [0] * 60 + [14] * 60                                                  # 마지막 60분 실패율 1.4% = 번레이트 14×
    f = slo.firing_minutes(n, e, s, slo.AlertPolicy("t", page=(((60, 13.5), (5, 13.5)),), ticket=(((60, 99.0),),)))
    assert f["page"][-1] and not f["page"][59] and not any(f["ticket"])


def test_episodes_merge_consecutive_minutes() -> None:
    assert slo.episodes([False, True, True, False, True]) == [(1, 3), (4, 5)]
    assert slo.episodes([]) == [] and slo.episodes([True, True]) == [(0, 2)]


def test_multi_window_burn_pages_for_real_outages_and_ignores_a_two_minute_blip() -> None:
    inc = slo.STANDARD_INCIDENTS
    n, e = slo.simulate(days=28, seed=700, incidents=inc, rpm=60)
    burn = slo.score_alerts(slo.firing_minutes(n, e, slo.SLO("a"), slo.BURN), inc)
    naive = slo.score_alerts(slo.firing_minutes(n, e, slo.SLO("a"), slo.NAIVE), inc)
    assert burn["page"]["per_incident"]["hard_outage"]["delay_min"] is not None and burn["page"]["per_incident"]["hard_outage"]["delay_min"] < 15
    assert burn["page"]["per_incident"]["brief_blip"]["alerts"] == 0          # 순간 흔들림에는 page 가 없다
    assert burn["ticket"]["per_incident"]["slow_burn"]["delay_min"] is not None
    assert naive["page"]["per_incident"]["brief_blip"]["alerts"] >= 1         # 순진한 정책은 순간 흔들림에도 깨운다(악용/오탐 쪽 시험)


def test_simulation_is_deterministic() -> None:
    a = slo.simulate(days=2, seed=1, incidents=slo.STANDARD_INCIDENTS, rpm=30)
    b = slo.simulate(days=2, seed=1, incidents=slo.STANDARD_INCIDENTS, rpm=30)
    assert a == b and len(a[0]) == 2 * 1440


# --- 부하·격리(실제 서버, 아주 작게) ---------------------------------------------------------------------------------------------------


def test_live_server_serves_a_small_load_with_no_errors() -> None:
    from adgate.ops.load import ALICE, LiveServer, bodies, load_settings, run_clients, summarize

    with LiveServer(load_settings()) as srv:
        st = run_clients(srv.url, ALICE, bodies(POOL, 30), 4)
    s = summarize(st)
    assert s["requests"] == 30 and s["status"] == {"201": 30} and s["available"] == 1.0 and s["conn_errors"] == 0


def test_flooder_is_rate_limited_and_the_steady_tenant_is_not() -> None:
    from adgate.ops.load import ALICE, CAROL, LiveServer, bodies, load_settings, run_clients, summarize

    with LiveServer(load_settings(rate_per_min=40)) as srv:
        flood = summarize(run_clients(srv.url, ALICE, bodies(POOL, 120, "F"), 6))
        calm = summarize(run_clients(srv.url, CAROL, bodies(POOL, 10, "C"), 1))
    assert flood["rate_limited"] >= 70 and flood["status"].get("201", 0) <= 45         # 상한을 넘는 요청은 429
    assert calm["status"] == {"201": 10}                                                # 다른 신원의 호출은 영향이 없다


def test_judge_outage_guard_bounds_latency_after_the_circuit_opens() -> None:
    from adgate.ops.load import rehearse_judge_outage

    r = rehearse_judge_outage(POOL, delay_s=0.4, timeout_s=0.1, requests=14)
    assert r["guarded"]["after_first6_p95_ms"] < 100                                    # 회로가 열린 뒤에는 judge 를 기다리지 않는다
    assert r["unguarded"]["p50_ms"] > 300                                               # 보호가 없으면 요청마다 judge 의 지연을 그대로 낸다
    assert r["guarded"]["status"] == {"201": 14} and r["unguarded"]["conn_errors"] == 0
