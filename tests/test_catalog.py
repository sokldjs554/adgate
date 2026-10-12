"""라벨링 파이프라인(catalog/): 우선순위 큐, 이중 라벨 + 중재, 운영 환류, 사람 실험 키트. 사람 데이터는 없다 — 구조·불변식·알려진 효과의 복원을 시험한다."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from adgate.catalog import adjudicate, feedback, queue, study
from adgate.catalog.adjudicate import Label, LabelTask, blind_view, resolve
from adgate.eval import dataset
from adgate.models import Advertiser, Creative

DATA = Path(__file__).resolve().parents[1] / "data"
POOL = dataset.load(DATA / "redteam.jsonl") + dataset.load(DATA / "gray.jsonl")


def creative(**kw: Any) -> Creative:
    base: dict[str, Any] = {"ad_id": "c1", "advertiser": Advertiser(name="가상상점", category="general", verified=True), "headline": "봄맞이 신상", "body": "새 상품을 소개합니다"}
    return Creative(**{**base, **kw})


# --- 우선순위 큐 -----------------------------------------------------------------------------------------------------------------------


def test_cue_count_sees_through_spacing_tricks_and_ignores_plain_text() -> None:
    plain = dataset.Example(id="p", creative=creative(), gold=dataset.Gold(decision="APPROVE", violations=[], tags=[]))
    sneaky = dataset.Example(id="s", creative=creative(headline="수 익 보 장 · 국 내 1 위", body="오 늘 만"), gold=dataset.Gold(decision="REJECT", violations=[], tags=[]))
    assert queue.cue_count(plain) == 0 and queue.cue_count(sneaky) == 3          # 보장 · 최상급 · 긴급


def test_score_weights_follow_the_preregistered_table() -> None:
    s = queue.Signals(review=True, challenge_only=True, escalation=True, cues=3, prior=True, judge_disagree=True)
    assert queue.score(s, "engine") == 3 + 2 + 1
    assert queue.score(s, "engine+cues") == 3 + 2 + 1 + 3 + 0.5
    assert queue.score(s, "engine+cues+judge") == 3 + 2 + 1 + 3 + 0.5 + 2
    assert queue.score(queue.Signals(judge_disagree=None), "engine+cues+judge") == 0


def test_rank_orders_by_score_then_id_and_flags_errors() -> None:
    ranked = queue.rank(POOL[:60], "engine+cues")
    assert [r.score for r in ranked] == sorted((r.score for r in ranked), reverse=True)
    assert all(r.error == (r.outcome != r.gold) for r in ranked)
    assert queue.rank(POOL[:60], "engine+cues") == ranked                       # 결정론


def test_recall_auc_and_random_baseline_arithmetic() -> None:
    rows = [queue.Ranked(f"i{k}", float(10 - k), "APPROVE", "REJECT" if k in (0, 1, 8) else "APPROVE", k in (0, 1, 8), k in (0, 1, 8)) for k in range(10)]
    assert queue.recall_at(rows, 0.2, lambda r: r.error) == (2, 3)
    assert queue.auc(rows, lambda r: r.error) is not None and 0.5 < (queue.auc(rows, lambda r: r.error) or 0) < 1
    perfect = [queue.Ranked(f"i{k}", float(10 - k), "A", "B", k < 3, False) for k in range(10)]
    assert queue.auc(perfect, lambda r: r.error) == 1.0
    assert queue.auc(rows, lambda r: False) is None
    assert 0.15 < queue.random_recall(rows, 0.2, lambda r: r.error, draws=2000) < 0.25          # 무작위 기대치는 예산 비율 근처


def test_evaluate_queue_has_every_variant_that_can_be_measured() -> None:
    sets = {"redteam": POOL[:20], "gray": POOL[72:92], "dev": POOL[:5], "test": POOL[5:10]}
    res = queue.evaluate_queue(sets, judge=None)
    assert set(res) == {"redteam", "gray", "dev", "test", "independent"}
    ind: dict[str, Any] = res["independent"]  # type: ignore[assignment]
    assert set(ind) == {"engine", "engine+cues"}                                  # judge 녹음이 없으면 judge 변형은 재지 않는다
    assert ind["engine"]["n"] == 40 and set(ind["engine"]["top20"]) >= {"errors_found", "errors_total", "random_error_recall"}


# --- 이중 라벨 + 중재 ------------------------------------------------------------------------------------------------------------------


def task(i: int = 0) -> LabelTask:
    return LabelTask(f"T{i}", creative(), source="queue", priority=5.0)


def test_assignment_is_balanced_distinct_and_seeded() -> None:
    ids = [f"T{i}" for i in range(30)]
    a = adjudicate.assign(ids, ["a", "b", "c", "d", "e"], k=2, seed=3)
    assert all(len(set(v)) == 2 for v in a.values())
    load = {n: sum(n in v for v in a.values()) for n in "abcde"}
    assert max(load.values()) - min(load.values()) <= 1
    assert a == adjudicate.assign(ids, ["a", "b", "c", "d", "e"], k=2, seed=3) and a != adjudicate.assign(ids, ["a", "b", "c", "d", "e"], k=2, seed=4)
    with pytest.raises(ValueError):
        adjudicate.assign(ids, ["only"], k=2)


def test_labelers_never_see_the_engine_or_the_priority() -> None:
    view = blind_view(task())
    assert set(view) == {"task_id", "category", "advertiser_verified", "headline", "body", "cta", "image_text", "disclosure", "landing_text"}
    assert "priority" not in json.dumps(view) and "queue" not in json.dumps(view)


def test_resolve_covers_agreement_majority_and_dispute() -> None:
    a = Label("a", "REJECT", ("AD-GUA-01",), (("AD-GUA-01", "headline", "수익 보장"),))
    b = Label("b", "REJECT", ("AD-GUA-01", "AD-ABS-01"), (("AD-GUA-01", "headline", "수익 보장"), ("AD-ABS-01", "body", "최고")))
    r = resolve([a, b])
    assert r.status == "agreed" and r.decision == "REJECT" and r.rules == ("AD-GUA-01",)       # 둘 이상이 든 규칙만
    assert resolve([a, Label("c", "APPROVE")]).status == "needs_third"
    maj = resolve([a, Label("c", "APPROVE"), Label("d", "REJECT", ("AD-GUA-01",))])
    assert maj.status == "resolved_majority" and maj.decision == "REJECT" and maj.rules == ("AD-GUA-01",)
    dis = resolve([a, Label("c", "APPROVE"), Label("d", "REVIEW")])
    assert dis.status == "disputed" and dis.decision is None and dis.needs_policy_ruling
    assert resolve([Label("a", "APPROVE", ("AD-GUA-01",)), Label("b", "APPROVE", ("AD-GUA-01",))]).rules == ()   # 승인에는 규칙이 없다
    with pytest.raises(ValueError):
        resolve([a, Label("a", "REJECT")])


def test_promote_writes_provenance_and_never_into_frozen_files(tmp_path: Path) -> None:
    t_ok, t_open = task(1), task(2)
    t_ok.labels = [Label("a", "APPROVE"), Label("b", "APPROVE")]
    t_open.labels = [Label("a", "APPROVE"), Label("b", "REJECT")]
    out = tmp_path / "batch1.jsonl"
    assert adjudicate.promote([t_ok, t_open], out, batch="b1") == {"written": 1, "skipped_existing": 0, "unresolved": 1}
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert rec["gold"]["tags"][0] == "candidate" and rec["provenance"] == {"batch": "b1", "source": "queue", "labelers": ["a", "b"], "status": "agreed"}
    assert adjudicate.promote([t_ok], out, batch="b2")["skipped_existing"] == 1 and len(out.read_text(encoding="utf-8").splitlines()) == 1
    with pytest.raises(PermissionError):
        adjudicate.promote([t_ok], tmp_path / "redteam.jsonl", batch="b3", frozen={"redteam.jsonl", "gray.jsonl"})


def test_adjudication_simulation_is_monotone_and_accounts_for_every_task() -> None:
    lo, hi = adjudicate.simulate(3000, 0.8, seed=1), adjudicate.simulate(3000, 0.95, seed=1)
    assert hi["gold_accuracy"] > lo["gold_accuracy"] and hi["disputed_share"] < lo["disputed_share"]
    assert sum(hi["status"].values()) == 3000 and hi["resolved"] + hi["status"].get("disputed", 0) == 3000
    st = adjudicate.agreement_stats([_labeled("a", "APPROVE", "APPROVE"), _labeled("b", "REJECT", "APPROVE"), _labeled("c", "REVIEW", "REVIEW")])
    assert st["double_labeled"] == 3 and abs(st["raw_agreement"] - 2 / 3) < 1e-3 and st["status"]["needs_third"] == 1


def _labeled(i: str, x: str, y: str) -> LabelTask:
    t = task(0)
    t.id = i
    t.labels = [Label("a", x), Label("b", y)]
    return t


# --- 운영 환류 -------------------------------------------------------------------------------------------------------------------------


def test_feedback_turns_human_overrides_into_candidates_without_names(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from adgate.api import create_app
    from adgate.settings import DEV_TOKENS, Settings

    alice, bob = {"Authorization": "Bearer dev-advertiser"}, {"Authorization": "Bearer dev-reviewer"}
    clean = {"advertiser": {"name": "가상앱", "category": "app", "verified": True}, "headline": "가계부 앱", "body": "영수증을 찍으면 자동으로 분류돼요.", "cta": "다운로드"}
    app = create_app(Settings(principals=dict(DEV_TOKENS), dev=True, rate_per_min=1000))
    with TestClient(app) as c:
        ids = []
        for n, head in enumerate(["오늘만 이 가격", "오늘만 특가", "마감 임박 세일"]):
            r = c.post("/v1/reviews", json={**clean, "ad_id": f"f{n}", "headline": head}, headers=alice).json()
            assert r["outcome"] == "REVIEW"
            ids.append(r["run_id"])
        c.post(f"/v1/reviews/{ids[0]}/resume", json={"verdict": "APPROVE", "reason": "근거 확인함 (기간 명시)"}, headers=bob)
        c.post(f"/v1/reviews/{ids[1]}/resume", json={"verdict": "APPROVE", "reason": "랜딩에 날짜 있음"}, headers=bob)
        c.post(f"/v1/reviews/{ids[2]}/resume", json={"verdict": "REJECT", "reason": "근거 없는 긴급성"}, headers=bob)
        items = feedback.mine(app.state.store)
    assert [i.kind for i in items] == ["override_approve", "override_approve", "confirmed"]
    st = feedback.override_stats(items)
    assert st["human_decided"] == 3 and st["rules"]["AD-URG-01"] == {"seen": 3, "human_approved": 2, "override_rate": round(2 / 3, 4)}
    out = tmp_path / "cand.jsonl"
    assert feedback.export_candidates(items, out) == 2
    blob = out.read_text(encoding="utf-8")
    assert "bob" not in blob and "근거 확인함" not in blob and "reviewer_id" in blob          # 검토자 이름과 사유는 내보내지 않는다
    assert all(json.loads(ln)["source"] == "production_review" for ln in blob.splitlines())


def test_feedback_ignores_auto_decided_runs() -> None:
    from adgate.store import Store

    s = Store(":memory:")
    s.create_run(
        submitter="alice", creative=json.loads(creative().model_dump_json()), result={"outcome": "APPROVE", "findings": []}, state="completed", final_outcome="APPROVE",
        decided_by="auto", idempotency_key=None, request_hash=None, events=[("decided", {})],
    )
    assert feedback.mine(s) == [] and feedback.override_stats([])["override_approve_rate"] is None


# --- 사람 실험 키트 ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def items() -> list[study.StudyItem]:
    return study.select_items(POOL, per_stratum=8, seed=5)


def test_item_selection_is_stratified_seeded_and_excludes_disputed(items: list[study.StudyItem]) -> None:
    assert {s: sum(i.stratum == s for i in items) for s in study.STRATA} == dict.fromkeys(study.STRATA, 8)
    assert [i.id for i in items] == [i.id for i in study.select_items(POOL, per_stratum=8, seed=5)]
    disputed = {e.id for e in POOL if "disputed" in e.gold.tags}
    assert disputed and not disputed & {i.id for i in items}
    assert all((i.engine != i.gold) == (i.stratum == "engine_wrong") for i in items)
    with pytest.raises(ValueError):
        study.select_items(POOL[:30], per_stratum=50)


def test_design_is_counterbalanced(items: list[study.StudyItem]) -> None:
    trials = study.design(items, 8, seed=2)
    per_reviewer = {r: [t for t in trials if t.reviewer == r] for r in sorted({t.reviewer for t in trials})}
    assert all(len(v) == len(items) and len({t.item_id for t in v}) == len(items) for v in per_reviewer.values())     # 모두 모든 소재를 한 번
    for it in items:                                                                                                   # 모든 소재가 검토자 절반에게 제안과 함께
        assert sum(t.condition == "assisted" for t in trials if t.item_id == it.id) == 4
    for v in per_reviewer.values():                                                                                    # 검토자마다 절반씩, 각 블록은 층 구성이 같다
        assert sum(t.condition == "assisted" for t in v) == len(items) // 2
    firsts = [v[0].condition for v in per_reviewer.values()]
    assert firsts.count("assisted") == firsts.count("unassisted") == 4                                                 # 블록 순서도 맞바꿔졌다
    assert trials == study.design(items, 8, seed=2)


def test_booklet_hides_gold_and_unassisted_suggestions_and_escapes_script(items: list[study.StudyItem]) -> None:
    trials = study.design(items, 4, seed=1)
    by = {i.id: i for i in items}
    html = study.booklet("R01", trials, by, [], "d1")
    data = json.loads(html.split("const DATA = ", 1)[1].split(";\nconst app", 1)[0])
    mine = {t.item_id: t.condition for t in trials if t.reviewer == "R01"}
    for iid, blob in data["items"].items():
        assert set(blob) == {"creative", "engine", "rules", "quotes"}
        assert (blob["engine"] is not None) == (mine[iid] == "assisted")                           # 제안은 제안과 함께 보는 소재에만
        assert not blob["rules"] and not blob["quotes"] or mine[iid] == "assisted"
    assert '"gold"' not in html and '"stratum"' not in html and "http" not in html.replace("https://", "")
    evil = study.StudyItem("X", "clear_correct", {"headline": "</script><script>alert(1)</script>"}, "APPROVE", "APPROVE", (), ())
    page = study.booklet("R01", [study.Trial("R01", 1, "X", "assisted", 1)], {"X": evil}, [], "d1")
    assert page.count("</script>") == 1 and "\\u003c/script\\u003e" in page                       # 소재 안의 닫는 태그는 이스케이프된다


def test_parse_responses_validates() -> None:
    ok = {"reviewer": "R01", "responses": [{"item": "a", "condition": "assisted", "decision": "APPROVE", "ms": 1200}]}
    assert study.parse_responses(ok)[0].ms == 1200
    for bad in ({"item": "a", "condition": "x", "decision": "APPROVE", "ms": 1}, {"item": "a", "condition": "assisted", "decision": "MAYBE", "ms": 1}):
        with pytest.raises(ValueError):
            study.parse_responses({"reviewer": "R01", "responses": [bad]})


def test_exact_sign_flip_permutation_p_value() -> None:
    r = study._paired([0.1, 0.2, 0.15, 0.3, 0.05], boot=0, perm=0, seed=0)
    assert r["p"] == pytest.approx(2 / 32) and r["mean"] == pytest.approx(0.16)
    assert study._paired([0.1], boot=0, perm=0, seed=0)["p"] is None
    assert study._paired([0.0, 0.0, 0.0, 0.0], boot=0, perm=0, seed=0)["p"] == 1.0


def test_analysis_recovers_known_effects_from_simulated_reviewers(items: list[study.StudyItem]) -> None:
    by = {i.id: i for i in items}
    trials = study.design(items, 40, seed=7)
    strong = study.SimParams(help_when_engine_right=0.0, follow_when_engine_wrong=0.5, speedup=0.7)
    res = study.analyze(study.simulate_responses(trials, by, strong, seed=11), by, boot=500, perm=5000, seed=1)
    m = res["metrics"]
    assert m["follow_wrong"]["ci_low"] > 0 and m["follow_wrong"]["p"] < 0.01                   # 제안을 따르는 자동화 편향이 잡힌다
    assert m["log_time"]["ci_low"] < math.log(0.7) < m["log_time"]["ci_high"]                  # 시간 배수 0.7 을 되찾는다(로그 척도)
    assert 0.6 < m["log_time"]["time_ratio"] < 0.8
    null = study.analyze(study.simulate_responses(trials, by, study.SimParams(), seed=12), by, boot=500, perm=5000, seed=1)["metrics"]
    assert null["follow_wrong"]["p"] > 0.01 and null["log_time"]["p"] > 0.01 and abs(null["log_time"]["mean"]) < 0.15       # 효과가 없으면 없다고 나온다


def test_zero_effect_studies_rarely_reach_significance(items: list[study.StudyItem]) -> None:
    res = study.power(items, study.SimParams(), reviewers=(8,), sims=40, seed=3)["by_reviewers"]["8"]
    assert all(v <= 0.2 for v in res.values())                                                  # 효과 0 이면 유의 비율이 5% 근처(느슨한 상한 20%)
