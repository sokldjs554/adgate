"""Judge 장애 격리(ops/guard.py): 시간 제한 · 벌크헤드 · 회로 차단기. 장애가 결정론 경로의 지연으로 번지지 않아야 한다."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from typing import Any

import pytest

from adgate.config import ReviewConfig
from adgate.judge import ProposedFinding
from adgate.judge_anthropic import JudgeError
from adgate.models import Advertiser, Creative
from adgate.ops.guard import BulkheadFull, CircuitOpen, GuardedJudge, JudgeTimeout
from adgate.pipeline import review
from adgate.policy import load_policy

POLICY = load_policy()
C = Creative(ad_id="g1", advertiser=Advertiser(name="가상상점", category="general", verified=True), headline="봄맞이 신상 안내", body="새로 나온 제품을 소개합니다")  # type: ignore[arg-type]


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class Flaky:
    name = "flaky"

    def __init__(self, fail: bool = True, delay: float = 0.0, gate: threading.Event | None = None) -> None:
        self.fail, self.delay, self.gate, self.calls = fail, delay, gate, 0

    def propose(self, creative: Creative, policy: Any, already: Sequence[dict[str, Any]] = ()) -> list[ProposedFinding]:
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise JudgeError("boom")
        return []


def trip(g: GuardedJudge, n: int) -> None:
    for _ in range(n):
        with pytest.raises(JudgeError):
            g.propose(C, POLICY, [])


def test_circuit_opens_after_consecutive_failures_and_short_circuits_without_calling() -> None:
    inner, clock = Flaky(), Clock()
    g = GuardedJudge(inner, failure_threshold=3, reset_s=30, clock=clock)
    trip(g, 3)
    assert g.state == "open" and inner.calls == 3
    with pytest.raises(CircuitOpen):
        g.propose(C, POLICY, [])
    assert inner.calls == 3 and g.counts["short_circuit"] == 1          # 열린 동안은 judge 를 부르지 않는다


def test_one_success_resets_the_failure_count() -> None:
    inner, clock = Flaky(), Clock()
    g = GuardedJudge(inner, failure_threshold=3, clock=clock)
    trip(g, 2)
    inner.fail = False
    g.propose(C, POLICY, [])
    inner.fail = True
    trip(g, 2)                                                           # 연속이 아니므로 아직 닫혀 있다
    assert g.state == "closed"


def test_half_open_admits_a_single_probe_and_recovers_or_reopens() -> None:
    inner, clock = Flaky(), Clock()
    g = GuardedJudge(inner, failure_threshold=2, reset_s=30, clock=clock)
    trip(g, 2)
    clock.t = 31
    assert g.state == "half_open"
    # 시험 호출이 실패하면 다시 열린다(한 번의 실패로 충분하다)
    with pytest.raises(JudgeError):
        g.propose(C, POLICY, [])
    assert g.state == "open" and inner.calls == 3
    clock.t = 70
    inner.fail = False
    assert g.propose(C, POLICY, []) == [] and g.state == "closed"        # 시험 호출이 성공하면 닫힌다
    g.propose(C, POLICY, [])
    assert inner.calls == 5


def test_only_one_probe_runs_at_a_time_in_half_open() -> None:
    gate = threading.Event()
    inner, clock = Flaky(fail=False, gate=gate), Clock()
    g = GuardedJudge(inner, failure_threshold=1, reset_s=10, clock=clock, timeout_s=5, max_concurrent=4)
    inner.fail = True
    gate.set()
    trip(g, 1)
    clock.t = 11
    gate.clear()
    inner.fail = False
    out: list[Any] = []
    t = threading.Thread(target=lambda: out.append(g.propose(C, POLICY, [])))
    t.start()
    for _ in range(200):                                                 # 시험 호출이 judge 안에서 막힐 때까지
        if inner.calls == 2:
            break
        time.sleep(0.005)
    with pytest.raises(CircuitOpen):                                     # 시험 호출이 끝나기 전의 다른 호출은 통과하지 못한다
        g.propose(C, POLICY, [])
    gate.set()
    t.join(5)
    assert out == [[]] and g.state == "closed"


def test_timeout_bounds_latency_and_counts_as_a_failure() -> None:
    inner = Flaky(fail=False, delay=0.5)
    g = GuardedJudge(inner, timeout_s=0.05, failure_threshold=2)
    t0 = time.perf_counter()
    with pytest.raises(JudgeTimeout):
        g.propose(C, POLICY, [])
    assert time.perf_counter() - t0 < 0.3 and g.counts["timeout"] == 1
    g.close()


def test_bulkhead_rejects_instead_of_queueing_behind_hung_calls() -> None:
    gate = threading.Event()
    inner = Flaky(fail=False, gate=gate)
    g = GuardedJudge(inner, timeout_s=0.05, max_concurrent=1, failure_threshold=10)
    with pytest.raises(JudgeTimeout):
        g.propose(C, POLICY, [])                                         # 첫 호출이 judge 안에서 걸려 슬롯을 쥔다
    t0 = time.perf_counter()
    with pytest.raises(BulkheadFull):
        g.propose(C, POLICY, [])                                         # 두 번째는 기다리지 않고 바로 거절된다
    assert time.perf_counter() - t0 < 0.05
    gate.set()
    for _ in range(200):                                                 # 걸려 있던 호출이 끝나면 슬롯이 돌아온다
        try:
            g.propose(C, POLICY, [])
            break
        except BulkheadFull:
            time.sleep(0.005)
    else:
        pytest.fail("슬롯이 돌아오지 않았다")
    g.close()


def test_pipeline_stays_fail_closed_and_fast_when_the_judge_hangs() -> None:
    inner = Flaky(fail=False, delay=0.6)
    g = GuardedJudge(inner, timeout_s=0.05, failure_threshold=2)
    t0 = time.perf_counter()
    r = review(C, ReviewConfig(judge=g), POLICY)
    assert time.perf_counter() - t0 < 0.4
    assert r.outcome == "REVIEW" and "judge_unavailable" in r.escalations         # 승인하지 않고 사람에게 넘긴다
    base = review(C, ReviewConfig(), POLICY)
    assert base.outcome == "APPROVE"                                                 # 결정론 경로는 judge 없이 그대로 승인한다
    g.close()


def test_a_clear_violation_stays_rejected_when_the_judge_is_down() -> None:
    bad = C.model_copy(update={"headline": "원금 보장 무조건 수익"})
    inner, clock = Flaky(), Clock()
    g = GuardedJudge(inner, failure_threshold=1, clock=clock)
    r1 = review(bad.model_copy(update={"advertiser": Advertiser(name="가상투자", category="invest", verified=True)}), ReviewConfig(judge=g), POLICY)
    assert r1.outcome == "REJECT"                                                    # judge 장애가 결정론의 반려를 되돌리지 못한다


def test_constructor_rejects_nonsense() -> None:
    for kw in ({"timeout_s": 0}, {"max_concurrent": 0}, {"failure_threshold": 0}, {"reset_s": 0}):
        with pytest.raises(ValueError):
            GuardedJudge(Flaky(), **kw)


# --- API 연결 ---------------------------------------------------------------------------------------------------------------------------


def test_api_wraps_the_judge_and_a_dead_judge_does_not_slow_reviews() -> None:
    from fastapi.testclient import TestClient

    from adgate.api import create_app
    from adgate.settings import DEV_TOKENS, Settings

    alice = {"Authorization": "Bearer dev-advertiser"}
    clean = {"ad_id": "a1", "advertiser": {"name": "가상앱", "category": "app", "verified": True}, "headline": "가계부 앱", "body": "영수증을 찍으면 자동으로 분류돼요.", "cta": "다운로드"}
    inner = Flaky(fail=False, delay=0.4)
    s = Settings(principals=dict(DEV_TOKENS), dev=True, rate_per_min=1000, judge=inner, judge_timeout_s=0.05, judge_failure_threshold=2, judge_reset_s=60)
    with TestClient(create_app(s)) as c:
        t0 = time.perf_counter()
        r = c.post("/v1/reviews", json=clean, headers=alice)
        assert r.status_code == 201 and r.json()["outcome"] == "REVIEW"                      # judge 가 늦으면 승인하지 않고 사람에게 간다
        assert time.perf_counter() - t0 < 0.35
        c.post("/v1/reviews", json={**clean, "ad_id": "a2"}, headers=alice)                  # 두 번째 실패로 회로가 열린다
        calls = inner.calls
        t1 = time.perf_counter()
        r = c.post("/v1/reviews", json={**clean, "ad_id": "a3"}, headers=alice)
        assert r.status_code == 201 and time.perf_counter() - t1 < 0.2 and inner.calls == calls   # 열린 회로는 judge 를 부르지 않는다
        text = c.get("/metrics", headers=alice).text
        assert "adgate_judge_circuit 2" in text and 'adgate_judge_guard_events{event="short_circuit"}' in text


def test_api_without_a_judge_is_unchanged() -> None:
    from fastapi.testclient import TestClient

    from adgate.api import create_app
    from adgate.settings import DEV_TOKENS, Settings

    with TestClient(create_app(Settings(principals=dict(DEV_TOKENS), dev=True))) as c:
        assert "adgate_judge_circuit" not in c.get("/metrics", headers={"Authorization": "Bearer dev-advertiser"}).text
