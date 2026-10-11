from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from adgate.api import create_app
from adgate.settings import DEV_TOKENS, Settings

ALICE = {"Authorization": "Bearer dev-advertiser"}
CAROL = {"Authorization": "Bearer dev-advertiser-2"}
BOB = {"Authorization": "Bearer dev-reviewer"}

CLEAN = {
    "ad_id": "a1", "advertiser": {"name": "가상앱", "category": "app", "verified": True},
    "headline": "가계부 앱", "body": "영수증을 찍으면 자동으로 분류돼요.", "cta": "다운로드",
}
BAD = {**CLEAN, "ad_id": "a2", "headline": "무조건 승인"}
REVIEWABLE = {**CLEAN, "ad_id": "a3", "headline": "오늘만 이 가격"}  # URG → REVIEW


@pytest.fixture
def client():
    app = create_app(Settings(principals=dict(DEV_TOKENS), dev=True, rate_per_min=1000))
    with TestClient(app) as c:
        yield c


def submit(client, body, headers=ALICE, **kw):
    return client.post("/v1/reviews", json=body, headers=headers, **kw)


def test_auth_is_required_and_errors_are_problem_json(client):
    r = client.post("/v1/reviews", json=CLEAN)
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/problem+json")
    assert r.headers["www-authenticate"] == "Bearer"
    assert client.post("/v1/reviews", json=CLEAN, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/me", headers=ALICE).json() == {"identity": "alice", "role": "advertiser"}


def test_submit_outcomes_and_states(client):
    ok, bad, rev = submit(client, CLEAN).json(), submit(client, BAD).json(), submit(client, REVIEWABLE).json()
    assert (ok["final_outcome"], ok["state"], ok["decided_by"]) == ("APPROVE", "completed", "auto")
    assert (bad["final_outcome"], bad["state"]) == ("REJECT", "completed")
    assert (rev["final_outcome"], rev["state"], rev["decided_by"]) == ("REVIEW", "waiting_input", "pending")
    assert bad["result"]["reasons"][0]["rule_id"] == "AD-GUA-01"


def test_unknown_fields_and_bad_enums_are_rejected_with_422(client):
    r = submit(client, {**CLEAN, "extra": 1})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    r = submit(client, {**CLEAN, "advertiser": {"name": "x", "category": "weapons", "verified": True}})
    assert r.status_code == 422


def test_oversized_text_and_body_are_rejected(client):
    assert submit(client, {**CLEAN, "headline": "가" * 2001}).status_code == 422
    big = {**CLEAN, "landing_text": "가" * 200_000}
    assert submit(client, big).status_code == 413


def test_advertisers_cannot_read_each_others_runs_but_reviewers_can(client):
    run = submit(client, BAD).json()
    assert client.get(f"/v1/reviews/{run['run_id']}", headers=ALICE).status_code == 200
    assert client.get(f"/v1/reviews/{run['run_id']}", headers=CAROL).status_code == 404  # 403 이 아니라 404
    assert client.get(f"/v1/reviews/{run['run_id']}/events", headers=CAROL).status_code == 404
    assert client.post(f"/v1/reviews/{run['run_id']}/rewrite", headers=CAROL).status_code == 404
    seen = client.get(f"/v1/reviews/{run['run_id']}", headers=BOB).json()
    assert seen["submitter"] == "alice"
    assert "submitter" not in client.get(f"/v1/reviews/{run['run_id']}", headers=ALICE).json()


def test_listing_is_scoped_to_the_caller(client):
    submit(client, BAD, ALICE)
    submit(client, CLEAN, CAROL)
    assert {i["creative"]["ad_id"] for i in client.get("/v1/reviews", headers=ALICE).json()["items"]} == {"a2"}
    assert {i["creative"]["ad_id"] for i in client.get("/v1/reviews", headers=BOB).json()["items"]} == {"a1", "a2"}
    assert client.get("/v1/reviews?state=bogus", headers=BOB).status_code == 422


def test_resume_flow_and_separation_of_duties(client):
    run = submit(client, REVIEWABLE).json()
    rid = run["run_id"]
    body = {"verdict": "APPROVE", "reason": "랜딩에서 이벤트 기간을 확인했습니다."}
    assert client.post(f"/v1/reviews/{rid}/resume", json=body, headers=ALICE).status_code == 403   # 광고주는 불가
    assert client.post(f"/v1/reviews/{rid}/resume", json={"verdict": "APPROVE", "reason": "ok"}, headers=BOB).status_code == 422
    assert client.post(f"/v1/reviews/{rid}/resume", json={**body, "verdict": "MAYBE"}, headers=BOB).status_code == 422
    done = client.post(f"/v1/reviews/{rid}/resume", json=body, headers=BOB)
    assert done.status_code == 200
    j = done.json()
    assert (j["state"], j["final_outcome"], j["decided_by"]) == ("completed", "APPROVE", "human")
    assert j["outcome"] == "REVIEW"  # 자동 판정 기록은 보존된다
    assert client.post(f"/v1/reviews/{rid}/resume", json=body, headers=BOB).status_code == 409   # 두 번째는 실패
    types = [e["type"] for e in client.get(f"/v1/reviews/{rid}/events", headers=BOB).json()["events"]]
    assert types[0] == "submitted" and types[-1] == "resumed" and "waiting_input" in types


def test_a_reviewer_cannot_decide_their_own_submission(client):
    run = submit(client, REVIEWABLE, headers=BOB).json()
    body = {"verdict": "APPROVE", "reason": "스스로 승인하려는 시도입니다."}
    r = client.post(f"/v1/reviews/{run['run_id']}/resume", json=body, headers=BOB)
    assert r.status_code == 403 and "직무 분리" in r.json()["title"]


def test_only_waiting_runs_can_be_resumed(client):
    run = submit(client, BAD).json()
    r = client.post(f"/v1/reviews/{run['run_id']}/resume", json={"verdict": "APPROVE", "reason": "반려를 뒤집으려는 시도"}, headers=BOB)
    assert r.status_code == 409


def test_concurrent_resume_succeeds_exactly_once(client):
    rid = submit(client, REVIEWABLE).json()["run_id"]
    codes: list[int] = []

    def go(i: int) -> None:
        r = client.post(f"/v1/reviews/{rid}/resume", json={"verdict": "REJECT", "reason": f"동시 결정 시도 {i}"}, headers=BOB)
        codes.append(r.status_code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes).count(200) == 1 and all(c in (200, 409) for c in codes)


def test_idempotency_replay_and_conflict(client):
    h = {**ALICE, "Idempotency-Key": "abc-123"}
    first = client.post("/v1/reviews", json=BAD, headers=h)
    again = client.post("/v1/reviews", json=BAD, headers=h)
    assert first.status_code == 201 and again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true" and again.json()["run_id"] == first.json()["run_id"]
    other = client.post("/v1/reviews", json=CLEAN, headers=h)
    assert other.status_code == 409
    # 다른 사용자의 같은 키는 별개다
    assert client.post("/v1/reviews", json=BAD, headers={**CAROL, "Idempotency-Key": "abc-123"}).status_code == 201
    assert client.post("/v1/reviews", json=BAD, headers={**ALICE, "Idempotency-Key": "bad key!"}).status_code == 422
    assert len(client.get("/v1/reviews", headers=ALICE).json()["items"]) == 1


def test_concurrent_submits_with_one_key_create_one_run(client):
    """같은 키로 동시에 6건: 정확히 하나만 새로 만들어지고(201) 나머지는 같은 실행의 재생(200)이어야 한다. 스레드 예외도 없어야 한다."""
    h = {**ALICE, "Idempotency-Key": "race-1"}
    results: list[tuple[int, str, str | None]] = []
    errors: list[BaseException] = []

    def go() -> None:
        try:
            r = client.post("/v1/reviews", json=BAD, headers=h)
            results.append((r.status_code, r.json()["run_id"], r.headers.get("Idempotent-Replayed")))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(results) == 6
    assert sorted(code for code, _, _ in results) == [200] * 5 + [201]
    assert len({rid for _, rid, _ in results}) == 1
    assert all(replayed == "true" for code, _, replayed in results if code == 200)
    assert len(client.get("/v1/reviews", headers=ALICE).json()["items"]) == 1


def test_rewrite_endpoint_returns_a_verified_candidate_and_logs_it(client):
    bad = {**CLEAN, "headline": "국내 1위 가계부 앱"}
    run = submit(client, bad).json()
    r = client.post(f"/v1/reviews/{run['run_id']}/rewrite", headers=ALICE)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "verified" and j["candidate"]["headline"] == "가계부 앱"
    assert j["verification"]["outcome_after"] == "APPROVE"
    types = [e["type"] for e in client.get(f"/v1/reviews/{run['run_id']}/events", headers=ALICE).json()["events"]]
    assert "rewrite_requested" in types


def test_rate_limit():
    app = create_app(Settings(principals=dict(DEV_TOKENS), dev=True, rate_per_min=3))
    with TestClient(app) as c:
        codes = [c.get("/v1/me", headers=ALICE).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_metrics_health_policy_and_security_headers(client):
    submit(client, BAD)
    assert client.get("/metrics").status_code == 401  # 규칙별 건수를 담으므로 인증이 필요하다(독립 리뷰 L2)
    m = client.get("/metrics", headers=ALICE).text
    assert 'adgate_reviews_total{outcome="REJECT"} 1' in m and 'adgate_findings_total{rule="AD-GUA-01"} 1' in m
    assert "adgate_review_latency_ms_count 1" in m
    h = client.get("/healthz")
    assert h.json()["policy_version"] == "v1" and h.headers["x-content-type-options"] == "nosniff"
    assert "dev" not in h.json()  # 데모 토큰이 켜져 있다는 사실을 알리지 않는다
    assert "default-src 'self'" in h.headers["content-security-policy"]
    pol = client.get("/v1/policy").json()
    assert "AD-GUA-01" in pol["rules"] and pol["priority"][0] == "AD-FRD-01"
    assert client.get("/v1/reviews/x", headers=ALICE).headers["cache-control"] == "no-store"


def test_server_refuses_to_start_without_tokens(monkeypatch):
    monkeypatch.delenv("ADGATE_TOKENS", raising=False)
    monkeypatch.delenv("ADGATE_DEV", raising=False)
    with pytest.raises(RuntimeError):
        Settings.from_env()
    monkeypatch.setenv("ADGATE_TOKENS", "t1:reviewer:zoe")
    s = Settings.from_env()
    assert s.authenticate("t1") is not None and s.authenticate("t2") is None and not s.dev
    monkeypatch.setenv("ADGATE_TOKENS", "t1:root:zoe")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_judge_failure_sends_the_run_to_waiting_input():
    from adgate.judge import ScriptedJudge

    app = create_app(Settings(principals=dict(DEV_TOKENS), dev=True, judge=ScriptedJudge(raises=TimeoutError("x"))))
    with TestClient(app) as c:
        j = submit(c, CLEAN).json()
    assert (j["state"], j["final_outcome"]) == ("waiting_input", "REVIEW")
    assert j["result"]["escalations"] == ["judge_unavailable"]



# --------------------------------------------------------------------------- 독립 리뷰 반영 (docs/REVIEW.md)
LOAN_AD = {
    "ad_id": "L1", "advertiser": {"name": "가상캐피탈", "category": "loan", "verified": True},
    "headline": "직장인 신용대출 간편 신청", "body": "모바일로 한도를 확인하세요.", "cta": "한도 조회",
    "disclosure": "개인신용평점이 하락할 수 있습니다. 금리는 연 7.9%~15.9%입니다.",
}


def test_an_advertiser_cannot_claim_verification_the_platform_did_not_grant(client):
    """H6: 소재의 `verified: true` 는 주장일 뿐이다. 인증 여부는 인증된 계정에서 온다."""
    alice = submit(client, LOAN_AD, ALICE).json()           # 인증된 광고주
    carol = submit(client, {**LOAN_AD, "ad_id": "L2"}, CAROL).json()  # 같은 소재를 인증 안 된 계정이 제출
    assert alice["final_outcome"] == "APPROVE" and alice["creative"]["advertiser"]["verified"] is True
    assert carol["final_outcome"] == "REJECT" and carol["creative"]["advertiser"]["verified"] is False
    assert [r["rule_id"] for r in carol["result"]["reasons"]] == ["AD-ADV-01"]


def test_idempotency_is_judged_on_the_submitted_body_not_the_capped_one(client):
    keyed = {**CAROL, "Idempotency-Key": "k-1"}
    first = submit(client, {**LOAN_AD, "ad_id": "L3"}, keyed)
    again = submit(client, {**LOAN_AD, "ad_id": "L3"}, keyed)
    assert first.status_code == 201 and again.status_code == 200 and again.headers["idempotent-replayed"] == "true"


def test_reviewer_queue_is_oldest_first_and_pageable(client):
    ids = [submit(client, {**REVIEWABLE, "ad_id": f"q{i}"}, ALICE).json()["run_id"] for i in range(5)]
    page1 = client.get("/v1/reviews?state=waiting_input&limit=2", headers=BOB).json()
    assert [x["run_id"] for x in page1["items"]] == ids[:2] and page1["total"] == 5 and page1["next_offset"] == 2
    page2 = client.get("/v1/reviews?state=waiting_input&limit=2&offset=2", headers=BOB).json()
    assert [x["run_id"] for x in page2["items"]] == ids[2:4] and page2["next_offset"] == 4
    last = client.get("/v1/reviews?state=waiting_input&limit=2&offset=4", headers=BOB).json()
    assert [x["run_id"] for x in last["items"]] == ids[4:] and last["next_offset"] is None
    newest = client.get("/v1/reviews?state=waiting_input&order=newest&limit=1", headers=BOB).json()
    assert newest["items"][0]["run_id"] == ids[-1]
    assert client.get("/v1/reviews?order=sideways", headers=BOB).status_code == 422


def test_one_advertiser_cannot_bury_the_review_queue():
    app = create_app(Settings(principals=dict(DEV_TOKENS), dev=True, rate_per_min=1000, max_waiting_per_submitter=3))
    with TestClient(app) as c:
        codes = [submit(c, {**REVIEWABLE, "ad_id": f"b{i}"}, ALICE).status_code for i in range(5)]
        assert codes == [201, 201, 201, 429, 429]
        assert submit(c, {**REVIEWABLE, "ad_id": "c0"}, CAROL).status_code == 201  # 다른 광고주는 영향받지 않는다
        assert submit(c, CLEAN, ALICE).status_code == 201                          # 대기열에 안 들어가는 건은 막지 않는다


def test_events_do_not_expose_the_reviewer_to_the_advertiser(client):
    run = submit(client, REVIEWABLE, ALICE).json()
    r = client.post(f"/v1/reviews/{run['run_id']}/resume", json={"verdict": "APPROVE", "reason": "내부 메모: 과거 위반 이력 있음, 일단 승인"}, headers=BOB)
    assert r.status_code == 200
    as_alice = client.get(f"/v1/reviews/{run['run_id']}/events", headers=ALICE).json()["events"]
    text = str(as_alice)
    assert "bob" not in text and "내부 메모" not in text
    assert next(e for e in as_alice if e["type"] == "resumed")["payload"] == {"verdict": "APPROVE"}
    as_bob = client.get(f"/v1/reviews/{run['run_id']}/events", headers=BOB).json()["events"]
    assert next(e for e in as_bob if e["type"] == "resumed")["payload"]["reviewer"] == "bob"


def test_internal_notes_are_visible_to_reviewers_only(client):
    run = submit(client, BAD, ALICE).json()
    assert all("note" not in f for f in run["result"]["findings"])
    as_bob = client.get(f"/v1/reviews/{run['run_id']}", headers=BOB).json()
    assert any(f.get("note", "").startswith("pattern:") for f in as_bob["result"]["findings"])


def test_docs_page_may_load_swagger_from_the_cdn_but_the_console_may_not(client):
    assert "cdn.jsdelivr.net" in client.get("/docs").headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in client.get("/").headers["content-security-policy"]


def test_demo_tokens_cannot_be_mixed_with_real_ones(monkeypatch):
    monkeypatch.setenv("ADGATE_TOKENS", "t1:reviewer:zoe")
    monkeypatch.setenv("ADGATE_DEV", "1")
    with pytest.raises(RuntimeError, match="ADGATE_DEV"):
        Settings.from_env()


def test_verified_role_suffix_is_parsed(monkeypatch):
    monkeypatch.delenv("ADGATE_DEV", raising=False)
    monkeypatch.setenv("ADGATE_TOKENS", "a:advertiser+verified:acme,b:advertiser:newbie,r:reviewer:zoe")
    s = Settings.from_env()
    assert s.authenticate("a").verified and not s.authenticate("b").verified  # type: ignore[union-attr]
    monkeypatch.setenv("ADGATE_TOKENS", "a:advertiser+gold:acme")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_serve_refuses_public_demo_tokens_on_a_non_loopback_host(monkeypatch, capsys):
    from adgate.cli import main

    monkeypatch.delenv("ADGATE_TOKENS", raising=False)
    monkeypatch.setenv("ADGATE_DEV", "1")
    monkeypatch.delenv("ADGATE_DEV_ALLOW_PUBLIC", raising=False)
    assert main(["serve", "--host", "0.0.0.0", "--port", "8123"]) == 2
    assert "ADGATE_DEV_ALLOW_PUBLIC" in capsys.readouterr().err
