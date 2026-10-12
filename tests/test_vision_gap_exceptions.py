"""이미지 셋(vision) 첫 접촉에서 정답 글자를 *넣어도* 틀린 소재 7건 중 6건에 대한 좁은 수정과 그 *악용* 시험(docs/DECISIONS.md D-12, docs/FAILURES.md F-31).

수정마다 (가) 위반을 흉내 내는 악용 사례와 (나) 정상 유사 사례를 같이 시험한다. 정상 유사 중 일부는 독립 회색지대 셋(G-…)의 소재다 — 고치기 전에는
조사 공백 덕에 우연히 통과하던 경고문이어서, 조사 공백을 고치면 오히려 오반려가 늘 뻔했다(그래서 경고 문형을 함께 열었다).
"""

from __future__ import annotations

import pytest

from adgate.config import ReviewConfig
from adgate.eval.dataset import load
from adgate.models import Creative
from adgate.pipeline import review
from adgate.vision.dataset import load_vision


def run(text: str, *, field: str = "body", category: str = "general", cfg: ReviewConfig | None = None):
    d = {"ad_id": "x", "advertiser": {"name": "가상회사", "category": category, "verified": True},
         "headline": "안내", "body": "안내", "cta": "자세히 보기", field: text}
    r = review(Creative.model_validate(d), cfg or ReviewConfig())
    return r.outcome, [(f.rule_id, f.quote) for f in r.findings if f.quote]


def rules(text: str, **kw) -> set[str]:
    return {r for r, _ in run(text, **kw)[1]}


# --------------------------------------------------------------------------- 1. 감량 보장 (V-B03)
@pytest.mark.parametrize("text", [
    "3주 만에 체중 8kg 감량을 보장합니다",     # V-B03 의 작은 글씨
    "10kg 감량을 확약합니다",
    "체중 감량 보증 프로그램",
])
def test_a_weight_loss_guarantee_is_a_guarantee(text):
    assert "AD-GUA-01" in rules(text, category="supplement")


@pytest.mark.parametrize("text", [
    "감량을 보장하지 않습니다",
    "개인차가 있어 감량을 보장하지는 않습니다",
    "감량 보장 없음",
    "감량 결과는 개인차가 있으며 보장되지 않습니다",
    "감량 보증금 환급 챌린지, 목표를 달성하면 돌려드려요",   # '보증' 이 낱말('보증금')의 일부
    "체중 감량 후기를 모았습니다",
])
def test_weight_loss_lookalikes_are_not_guarantees(text):
    assert "AD-GUA-01" not in rules(text, category="supplement")


# --------------------------------------------------------------------------- 2. 범위를 설명하는 각주 (V-D07)
@pytest.mark.parametrize("text", [
    "※ 최고 할인율은 일부 모델에 한하며 무이자 조건은 카드사별로 다를 수 있습니다.",   # V-D07 의 작은 글씨
    "※ 최고 할인율은 조건에 따라 달라질 수 있습니다",
    "최고 할인율은 일부 모델에 한하며",
    "※ 최저 적립률은 일부 상품에 한해 적용됩니다",
])
def test_a_footnote_that_limits_a_headline_range_is_not_a_superlative(text):
    assert "AD-ABS-01" not in rules(text, category="ecommerce")


@pytest.mark.parametrize("text", [
    "※ 업계 최고 할인율은 일부 모델에 한하며",                 # 비교 범위가 앞에 붙은 순위 주장
    "시장 최고 할인율은 일부 모델에 한하며",                    # 어떤 범위 낱말이든 문장 첫머리가 아니면 주장
    "※ 역대 최고 할인율을 만나보세요",                         # 제한 서술이 없다
    "최고 할인율 보장",
    "최고 할인율은 저희가 드립니다",                            # 제한 서술이 없다
    "최고 할인율은 일부 모델에 한하며 업계 최고 수준입니다",   # 같은 문장에서 다시 주장
    "최고 할인율은 일부 모델에 한하며. 국내 1위 쇼핑몰",         # 면책 문장 뒤의 다른 주장
])
def test_superlative_claims_dressed_as_range_notes_are_still_caught(text):
    assert "AD-ABS-01" in rules(text, category="ecommerce")


def test_the_range_note_exception_can_be_switched_off():
    cfg = ReviewConfig(exceptions=False)
    assert "AD-ABS-01" in rules("※ 최고 할인율은 일부 모델에 한하며", category="ecommerce", cfg=cfg)


# --------------------------------------------------------------------------- 3. 원격 제어 앱 설치 (V-D08·V-F09, 회색지대 G-G11·G-E09·G-E07)
@pytest.mark.parametrize("text", [
    "휴대폰이 악성 앱에 감염되었습니다. 원격제어 앱을 설치하고 상담원의 안내를 따라 주세요.",   # V-D08
    "아래 버튼으로 원격 제어 앱을 설치해야 제거할 수 있습니다.",                                 # V-F09
    "점검 앱(APK)을 설치한 뒤 상담원의 원격 지원을 받아 주세요",                                  # G-G11
    "원격 지원 앱을 설치하면 상담사가 도와드립니다",
    "원격제어 앱 설치 후 상담",                                                                    # 조사 없는 꼴은 그대로 잡는다
    "APK 파일을 설치해 주세요",
])
def test_installing_a_remote_control_app_is_caught_with_or_without_the_object_particle(text):
    assert "AD-FRD-01" in rules(text)


@pytest.mark.parametrize("text", [
    "원격 제어 앱을 설치하라는 연락은 사기이니 바로 끊고 고객센터로 알려 주세요.",   # G-E07: 조사 공백 덕에 우연히 통과하던 경고문
    "원격제어 앱 설치하라는 연락은 사기입니다",
    "원격제어 앱을 설치하지 마세요",
    "APK 파일을 설치하지 마세요",
    "원격제어 앱 설치를 요구하지 않습니다",
    "원격제어 앱 설치 요구에는 응하지 마세요",
    "팀뷰어를 설치하라는 연락은 사기입니다",
])
def test_warnings_about_remote_control_apps_are_not_the_solicitation(text):
    assert "AD-FRD-01" not in rules(text)


@pytest.mark.parametrize("text", [
    "원격제어 앱을 설치하지 않으면 계좌가 정지됩니다",                                       # 협박 — 조건절은 부정이 아니다
    "원격제어 앱을 설치하지 않을 경우 환급이 취소됩니다",
    "원격제어 앱을 설치하라는 연락이 와서 상담을 받았는데 사기가 아니라 진짜 서비스입니다",   # 판정어가 먼 곳에서 부정된다
    "원격제어 앱을 설치하라는 연락은 사기라고 하지만 저희는 정식 서비스입니다",
    "원격제어 앱을 설치하라는 연락은 사기입니다만 저희 앱은 안전하니 설치하세요",             # '사기입니다만' 은 단언이 아니다
    "원격제어 앱 설치를 요구하지 않습니다만 설치해 주시면 빠릅니다",
    "원격제어 앱을 설치해야 하며 설치하라는 연락은 사기가 아닙니다",
    "원격제어 앱을 설치하세요. 설치 요구에는 응하지 마세요 라고 안내하는 곳은 가짜입니다",   # 명령이 먼저 나온다
])
def test_remote_control_solicitations_dressed_as_warnings_are_still_caught(text):
    assert "AD-FRD-01" in rules(text)


def test_the_remote_warning_exception_can_be_switched_off():
    assert "AD-FRD-01" in rules("원격제어 앱을 설치하지 마세요", cfg=ReviewConfig(exceptions=False))


# --------------------------------------------------------------------------- 4. 인증번호 회신 (V-E08)
@pytest.mark.parametrize("text", [
    "고객님의 택배가 주소 오류로 보관 중입니다. 재배송을 위해 문자로 받은 인증번호 6자리를 아래 번호로 회신해 주세요.",   # V-E08
    "비밀번호를 회신해 주세요",
    "OTP 번호를 답장으로 알려 주세요",
])
def test_asking_to_reply_with_a_verification_code_is_caught(text):
    assert "AD-FRD-01" in rules(text, category="ecommerce")


@pytest.mark.parametrize("text", [
    "인증번호를 회신하지 마세요",
    "인증번호는 누구에게도 회신하지 마세요",
    "인증번호를 입력하지 마세요",                    # 고치기 전에도 오반려였다 — '하지 마세요' 꼴을 못 읽었다
    "비밀번호를 입력하지 말고 앱에서 직접 확인하세요",
    "인증번호는 회신하지 않아도 됩니다",
])
def test_prohibitions_about_a_verification_code_are_not_a_request(text):
    assert "AD-FRD-01" not in rules(text, category="ecommerce")


@pytest.mark.parametrize("text", [
    "인증번호를 회신하지 않으면 택배가 반송됩니다",
    "인증번호를 입력하지 않으면 계정이 정지됩니다",   # 고치기 전에도 면책으로 읽혔다 — 접두 일치('하지 않')가 조건절까지 부정으로 봤다
    "인증번호를 입력하지 않을 경우 계정이 정지됩니다",
    "인증번호를 알려주지 않으면 계정이 정지됩니다",
])
def test_a_threat_with_a_conditional_clause_is_not_a_negation(text):
    assert "AD-FRD-01" in rules(text, category="ecommerce")


# --------------------------------------------------------------------------- 5. '요구에는 응하지 마세요' (V-E05)
@pytest.mark.parametrize("text", [
    "모르는 사람이 시키는 앱 설치와 선입금 요구에는 응하지 마세요",     # V-E05
    "선입금 요구에 응하지 마세요",
    "선입금 요구에는 절대 응하지 마세요",
    "선입금을 받지 않습니다",
])
def test_refusing_an_advance_fee_demand_is_not_the_demand(text):
    assert "AD-FRD-01" not in rules(text, category="loan")


@pytest.mark.parametrize("text", [
    "선입금하세요. 선입금 요구에는 응하지 마세요",                  # 앞 문장이 명령
    "선입금 필요 요구에는 응하지 마세요",                           # 같은 절에 요청 표지가 앞선다
    "선입금 후 대출 실행, 요구에는 응하지 마세요",                   # 쉼표로 절이 갈린다
    "선입금 요구에 응하세요",
    "선입금 요구에는 응하지 않으면 대출이 취소됩니다",               # 조건절
    "선입금을 받지 않으면 진행이 불가합니다",                        # '받지 않으면' 은 요구를 부정하는 말이 아니라 요구다
])
def test_advance_fee_demands_dressed_as_refusals_are_still_caught(text):
    assert "AD-FRD-01" in rules(text, category="loan")


# --------------------------------------------------------------------------- 6. 이 수정이 정상 소재·위반 소재에 준 영향
def test_the_vision_oracle_is_right_except_for_the_documented_policy_gap():
    """정답 글자를 넣었을 때 틀린 소재는 V-C08(이미지 속 카운트다운 타이머 — 정책에 시각 단서 규칙이 없다) 하나다."""
    items = load_vision("data/vision/vision.jsonl")
    wrong = {it.id for it in items if review(it.oracle().creative, ReviewConfig()).outcome != it.gold.decision}
    assert wrong == {"V-C08"}


def test_the_gray_items_that_depended_on_the_particle_gap_stay_right():
    by_id = {ex.id: ex for ex in load("data/gray.jsonl")}
    assert review(by_id["G-E07"].creative, ReviewConfig()).outcome == "APPROVE"   # 경고문: 우연이 아니라 문형으로 통과한다
    for gid in ("G-G11", "G-E09"):                                                 # APK 를 '을 설치' 로 쓴 사기 유도
        assert review(by_id[gid].creative, ReviewConfig()).outcome == by_id[gid].gold.decision == "REJECT"
