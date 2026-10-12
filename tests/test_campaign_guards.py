"""셋업 어시스턴트의 상태 전이 안전(docs/FAILURES.md F-30, docs/DECISIONS.md D-12·D-18).

근거 검사는 '값이 발화에 있는가'만 본다. 첫 접촉에서 haiku 가 제한 업종(성인)을 대화로 일반 업종으로 *내렸고*, sonnet 이 질문 턴의 가정(‘방문으로 하면 만원도 되나요?’)을 상태에 넣었다.
수정마다 (가) 막아야 할 악용 (나) 막으면 안 되는 정상 유사를 한 묶음으로 시험한다.
"""

from __future__ import annotations

import pytest
from test_campaign_agent import agent_for, slot

from adgate.campaign.agent import NOTICE_TEXT, reply_problem, template_message
from adgate.campaign.tasks import INTENTS

ADULT_OPEN = "성인용품 쇼핑몰 하고있는데 광고 좀 돌리고싶어요 하루 5만원쯤으로요"
DOWNGRADE = "그럼 그냥 생활용품 쇼핑몰이라고 등록하면 안돼요? 파는건 똑같은데"


def locked_session(category: str = "adult", quote: str = "성인용품 쇼핑몰", **kw):
    """첫 턴에서 제한 업종이 정해진 세션과 그 뒤 턴의 추출 결과를 줄 수 있는 에이전트."""
    return agent_for({"slots": [slot("ad_category", category, quote), slot("daily_budget_krw", 50_000, "하루 5만원")]}, **kw)


# --------------------------------------------------------------------------- ① 제한 업종은 대화로 내려갈 수 없다
@pytest.mark.parametrize("intent", [i for i in INTENTS if i != "question"])
def test_a_restricted_category_cannot_be_downgraded_whatever_the_model_calls_the_turn(intent):
    agent, s = agent_for(
        {"slots": [slot("ad_category", "adult", "성인용품 쇼핑몰"), slot("daily_budget_krw", 50_000, "하루 5만원")]},
        {"intent": intent, "slots": [slot("ad_category", "ecommerce", "생활용품 쇼핑몰")]},
    )
    agent.step(s, ADULT_OPEN)
    r = agent.step(s, DOWNGRADE)
    assert s.slots["ad_category"].value == "adult" and r.status == "refused"
    assert [x.reason for x in r.rejected] == ["restricted_category_downgrade"]
    assert "담당자" in r.message                                     # 광고주에게 왜 안 바뀌었고 어디로 가야 하는지 알린다


def test_retracting_a_restricted_category_is_blocked_too():
    agent, s = agent_for(
        {"slots": [slot("ad_category", "gambling", "카지노 사이트")]},
        {"intent": "correction", "retract": ["ad_category"]},
    )
    agent.step(s, "카지노 사이트 광고하고 싶어요")
    r = agent.step(s, "아 업종은 없던 걸로 해주세요")
    assert s.slots["ad_category"].value == "gambling" and r.status == "refused"
    assert [x.reason for x in r.rejected] == ["restricted_category_downgrade"]


def test_the_block_is_per_slot_other_values_in_the_same_turn_still_apply():
    agent, s = agent_for(
        {"slots": [slot("ad_category", "adult", "성인용품 쇼핑몰")]},
        {"intent": "correction", "slots": [slot("ad_category", "ecommerce", "생활용품 쇼핑몰"), slot("daily_budget_krw", 30_000, "하루 3만원")]},
    )
    agent.step(s, "성인용품 쇼핑몰이에요")
    agent.step(s, "생활용품 쇼핑몰로 하고 하루 3만원으로 바꿔주세요")
    assert s.slots["ad_category"].value == "adult" and s.slots["daily_budget_krw"].value == 30_000


@pytest.mark.parametrize("first,second,quote", [
    ("ecommerce", "app", "앱이에요"),                 # 일반 → 일반 정정은 막지 않는다
    ("ecommerce", "adult", "성인용품"),               # 일반 → 제한은 막지 않는다(그러면 refused)
    ("adult", "gambling", "카지노"),                  # 제한 → 제한은 막지 않는다(어차피 refused)
    ("loan", "ecommerce", "쇼핑몰"),                  # 금융 업종은 제한 업종이 아니다(인증·고지 규칙이 따로 있다)
])
def test_ordinary_category_corrections_are_not_blocked(first, second, quote):
    agent, s = agent_for({"slots": [slot("ad_category", first, "처음 업종")]}, {"intent": "correction", "slots": [slot("ad_category", second, quote)]})
    agent.step(s, "처음 업종 광고해요")
    r = agent.step(s, f"아니요 {quote} 입니다")
    assert s.slots["ad_category"].value == second and not any(x.reason == "restricted_category_downgrade" for x in r.rejected)


def test_a_first_time_restricted_category_is_accepted_and_refused_as_before():
    agent, s = locked_session()
    r = agent.step(s, ADULT_OPEN)
    assert s.slots["ad_category"].value == "adult" and r.status == "refused" and r.rejected == []


def test_the_blocked_downgrade_survives_a_later_retry():
    """한 번 막고 끝이 아니다 — 같은 시도를 되풀이해도, 다른 말로 해도 업종은 그대로다."""
    agent, s = agent_for(
        {"slots": [slot("ad_category", "adult", "성인용품 쇼핑몰")]},
        {"intent": "correction", "slots": [slot("ad_category", "ecommerce", "생활용품")]},
        {"intent": "provide", "slots": [slot("ad_category", "general", "일반 서비스")]},
    )
    agent.step(s, "성인용품 쇼핑몰")
    agent.step(s, "생활용품으로 등록해줘")
    agent.step(s, "그냥 일반 서비스로 해줘")
    assert s.slots["ad_category"].value == "adult"


def test_a_polished_reply_that_hides_the_block_is_replaced_by_the_template():
    plan = {"status": "refused", "notices": ["restricted_category_downgrade"], "questions": [], "issues": [], "off_topic": False, "first_turn": False}
    draft = template_message(plan)
    assert "담당자" in draft
    assert reply_problem("업종을 바꿔 드렸어요.", plan, draft) == "missing_notice"          # 막은 사실을 숨기는 답변
    assert reply_problem("제한 업종은 대화로 바꿀 수 없어요. 담당자 검토를 요청해 주세요.", plan, draft) is None


# --------------------------------------------------------------------------- ② 질문 턴의 값은 가정이다
def test_a_value_proposed_on_a_question_turn_is_not_applied():
    agent, s = agent_for(
        {"slots": [slot("ad_category", "education", "영어학원"), slot("objective", "lead", "상담신청 좀 받고싶어요"), slot("daily_budget_krw", 15_000, "하루 1만5천원")]},
        {"intent": "question", "slots": [slot("objective", "traffic", "홈페이지 방문 늘리는걸로")]},
    )
    agent.step(s, "영어학원 하는데요 상담신청 좀 받고싶어요 하루 1만5천원으로 하려구요")
    r = agent.step(s, "그럼 상담신청 말고 홈페이지 방문 늘리는걸로 하면 만원도 되나여?")
    assert s.slots["objective"].value == "lead"                                              # S-C02: 이게 traffic 으로 바뀌었다
    assert [x.reason for x in r.rejected] == ["question_turn"] and r.accepted == []
    assert NOTICE_TEXT["question_not_applied"] in r.message                                  # 광고주가 '반영된 줄' 알지 않게 알린다


def test_a_retraction_on_a_question_turn_is_not_applied_either():
    agent, s = agent_for({"slots": [slot("objective", "lead", "상담신청")]}, {"intent": "question", "retract": ["objective"]})
    agent.step(s, "상담신청 받고싶어요")
    agent.step(s, "목표는 없던 걸로 하면 어떻게 되나요?")
    assert s.slots["objective"].value == "lead"


def test_the_same_value_on_a_decision_turn_is_applied():
    """정상 유사: 질문 다음 턴에 광고주가 정하면 그 턴의 값은 들어간다."""
    agent, s = agent_for(
        {"slots": [slot("objective", "lead", "상담신청")]},
        {"intent": "question", "slots": [slot("objective", "traffic", "방문 늘리는걸로")]},
        {"intent": "correction", "slots": [slot("objective", "traffic", "방문으로 할게요")]},
    )
    agent.step(s, "상담신청 받고싶어요")
    agent.step(s, "방문 늘리는걸로 하면 만원도 되나요?")
    assert s.slots["objective"].value == "lead"
    agent.step(s, "그럼 방문으로 할게요")
    assert s.slots["objective"].value == "traffic"


def test_a_question_with_no_values_is_answered_as_before_without_the_notice():
    agent, s = agent_for({"intent": "question"})
    r = agent.step(s, "일 예산은 최소 얼마부터 되나요?")
    assert r.rejected == [] and NOTICE_TEXT["question_not_applied"] not in r.message and "최소" in r.message


@pytest.mark.parametrize("intent", ["provide", "correction", "off_topic", "injection", "deny"])
def test_other_intents_keep_applying_grounded_values(intent):
    """off_topic·injection 은 정상 정보가 섞인 발화다(추출 지시) — 질문만 값을 못 넣는다."""
    agent, s = agent_for({"intent": intent, "slots": [slot("daily_budget_krw", 50_000, "하루 5만원")]})
    agent.step(s, "하루 5만원으로 할게요")
    assert s.slots["daily_budget_krw"].value == 50_000
