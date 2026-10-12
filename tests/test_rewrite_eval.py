"""수정안 평가 하니스(docs/EVAL_REWRITE.md)의 집계가 정의대로인지 합성 소재 몇 건으로 시험한다. 평가셋은 읽지 않는다."""

from __future__ import annotations

from adgate.eval.dataset import Example, Gold, GoldViolation
from adgate.eval.rewrite_eval import NONFIXABLE, run, summarize
from adgate.models import Creative
from adgate.rewrite import AUTO_FIXABLE


def creative(**kw) -> Creative:
    base = {"ad_id": "x", "advertiser": {"name": "가상회사", "category": "invest", "verified": True},
            "headline": "안내", "body": "안내", "cta": "자세히 보기"}
    return Creative.model_validate({**base, **kw})


def example(id_: str, c: Creative, decision: str, violations: list[GoldViolation] | None = None) -> Example:
    return Example(id=id_, creative=c, gold=Gold(decision=decision, violations=violations or []))


FIX = example(
    "A-fix", creative(headline="가상투자 적립 서비스", body="매달 적립하면 수익을 보장합니다", cta="시작하기",
                      disclosure="투자 원금 손실이 발생할 수 있습니다. 과거 수익률은 미래를 보장하지 않습니다."),
    "REJECT", [GoldViolation(rule_id="AD-GUA-01", field="body", quote="수익을 보장합니다")],
)
LAUNDER = example(
    # 엔진이 잡는 위반(수익 보장) 하나와, 엔진이 놓치는 뜻 돌려 말하기 하나. 수정안은 엔진 기준으로는 통과하지만 정답 인용이 남는다.
    "A-launder", creative(headline="가상투자 적립 서비스", body="수익 보장 상품입니다. 손해 볼 일 없는 투자를 시작하세요", cta="시작하기",
                          disclosure="투자 원금 손실이 발생할 수 있습니다. 과거 수익률은 미래를 보장하지 않습니다."),
    "REJECT", [GoldViolation(rule_id="AD-GUA-01", field="body", quote="수익 보장"),
               GoldViolation(rule_id="AD-GUA-01", field="body", quote="손해 볼 일 없는 투자")],
)
NOFIX = example(
    "A-nofix", creative(headline="가상건강 관절 케어", body="관절염을 치료하는 건강기능식품입니다", cta="구매하기",
                        advertiser={"name": "가상건강", "category": "supplement", "verified": True}),
    "REJECT", [GoldViolation(rule_id="AD-HLT-01", field="body", quote="관절염을 치료하는")],
)
CLEAN = example("A-clean", creative(headline="가상투자 적립 서비스", body="매달 원하는 금액을 자동으로 적립합니다", cta="시작하기",
                                    disclosure="투자 원금 손실이 발생할 수 있습니다. 과거 수익률은 미래를 보장하지 않습니다."), "APPROVE")
FALSE_FLAG = example("A-false", creative(headline="가상투자 적립 서비스", body="수익을 보장합니다", cta="시작하기",
                                         disclosure="투자 원금 손실이 발생할 수 있습니다. 과거 수익률은 미래를 보장하지 않습니다."), "APPROVE")


def test_only_engine_flagged_examples_are_in_the_population():
    out = run({"s": [FIX, LAUNDER, NOFIX, CLEAN]})
    assert {i["id"] for i in out["items"]} == {"A-fix", "A-launder", "A-nofix"}
    assert out["sizes"] == {"s": 4}


def test_a_fixable_violation_gets_a_verified_rewrite_that_reviews_clean():
    item = next(i for i in run({"s": [FIX]})["items"])
    assert item["status"] == "verified"
    assert item["failed_checks"] == []
    assert item["verification"]["outcome_after"] == "APPROVE"
    assert item["laundered"] == []                                    # 정답 인용 '수익을 보장합니다' 가 지워졌다
    assert "수익을 보장합니다" not in item["candidate"]["body"]


def test_laundering_is_a_verified_rewrite_that_still_carries_a_gold_violation_quote():
    item = next(i for i in run({"s": [LAUNDER]})["items"])
    assert item["status"] == "verified"                                # 엔진은 둘째 위반을 못 본다
    assert [q["quote"] for q in item["laundered"]] == ["손해 볼 일 없는 투자"]
    s = summarize([item])
    assert s["laundered"]["k"] == 1 and s["laundered"]["n"] == 1 and s["laundered"]["ids"] == ["A-launder"]


def test_a_non_fixable_violation_is_handed_to_a_human_not_rewritten():
    item = next(i for i in run({"s": [NOFIX]})["items"])
    assert item["status"] == "unavailable"
    assert item["unavailable_reason"] == "no_fixable_finding"
    assert item["candidate"] is None
    s = summarize([item])
    assert s["unavailable_only_nonfixable"]["k"] == 1 and s["unavailable_only_nonfixable"]["n"] == 1


def test_false_flags_are_counted_apart_from_true_flags():
    out = run({"s": [FIX, FALSE_FLAG]})
    s = out["pooled"]
    assert s["true_flag"]["n"] == 1 and s["false_flag"]["n"] == 1
    assert s["false_flag"]["with_candidate"]["k"] == 1                  # 정상 소재에 대한 수정안도 따로 센다(R4)
    assert s["true_flag"]["verified"]["k"] == 1                         # 커버리지에 섞이지 않는다


def test_independent_summary_leaves_out_dev_and_test():
    out = run({"dev": [FIX], "test": [FIX], "redteam": [LAUNDER], "gray": [NOFIX]})
    assert out["pooled"]["flagged"] == 4
    assert out["independent"]["flagged"] == 2


def test_the_non_fixable_list_is_the_complement_of_the_generator_s_list():
    from adgate.policy import load_policy

    rules = {r for r in load_policy().rules if r != "AD-FMT-01"}
    assert set(NONFIXABLE) == rules - set(AUTO_FIXABLE)
