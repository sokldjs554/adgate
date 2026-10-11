"""레드팀 첫 접촉(docs/FAILURES.md F-8~F-12)이 찾은 공백의 회귀 테스트.

각 수정은 레드팀 사례 한 건에 맞춘 문구 패치가 아니라 *부류*를 고친 것이어야 한다. 그래서 사례의 문구 말고 같은 부류의 다른 변형과,
같은 장치가 정상 소재를 건드리지 않는지(음성 대조)를 함께 묶었다.
"""

from __future__ import annotations

import unicodedata

import pytest
from conftest import codes, mk

from adgate.config import ReviewConfig
from adgate.normalize import build
from adgate.pipeline import review

GUA = ("AD-GUA-01", "guarantee")
INV_OK = "투자 상품은 원금 손실이 발생할 수 있습니다."


def _quotes(c, rule):
    r = review(c, ReviewConfig(challenge=False))
    return [(f.field, f.quote) for f in r.findings if f.rule_id == rule and f.quote is not None]


# --------------------------------------------------------------------------- F-8 한자·유사 문자
@pytest.mark.parametrize("text", ["원금 保障형 상품", "無條件 승인", "收益 保障", "원금 保證", "百% 승인"])
def test_hanja_spellings_of_policy_words_are_folded(text):
    assert GUA in codes(mk(text, category="invest", disclosure=INV_OK))


@pytest.mark.parametrize("text", ["대한민국 國民 여러분", "第一 회 행사 안내", "金 사장님 가게"])
def test_unrelated_hanja_is_not_flagged(text):
    assert not codes(mk(text))


@pytest.mark.parametrize("text", ["саsіnо 게임", "сasino 이벤트", "casinо 보너스"])  # 키릴 с а і о 섞임
def test_cyrillic_lookalikes_are_folded_to_latin(text):
    assert ("AD-BRD-01", "restricted_content") in codes(mk("신규 가입", text))


def test_ordinary_cyrillic_text_is_not_flagged():
    assert not codes(mk("привет мир", "спасибо за заказ"))


# --------------------------------------------------------------------------- F-9 장식 기호 / 낱글자 띄우기
@pytest.mark.parametrize("text", ["오★늘★만", "오☆늘☆만", "오 ★ 늘 ★ 만", "오🔥늘🔥만", "오♥늘♥만"])
def test_decorative_symbols_between_syllables_are_removed(text):
    assert ("AD-URG-01", "urgency_unverified") in codes(mk("할인", cta=f"{text} 구매"))


@pytest.mark.parametrize("text", ["수♥익♥보♥장", "무🔥조🔥건 승인", "100■% 승인"])
def test_decorations_do_not_hide_guarantees(text):
    assert GUA in codes(mk(text, category="invest", disclosure=INV_OK))


@pytest.mark.parametrize("text", ["별점 ★★★★★ 후기 많아요", "※ 일부 제외", "→ 자세히 보기"])
def test_decorations_next_to_spaces_are_not_a_trigger(text):
    assert not codes(mk(text))


@pytest.mark.parametrize(
    "text", ["꾸준히 드시면 치 매 예 방에 도움이 됩니다.", "당 뇨 개 선에 도움", "암 예 방 효과", "고 혈 압 치 료"],
)
def test_letter_spaced_disease_claims_are_caught_by_the_soft_rules(text):
    assert ("AD-HLT-01", "disease_claim") in codes(mk("건강 습관", text, category="supplement"))


def test_letter_spaced_public_interest_context_is_seen():
    c = mk("혼자 견디지 마세요", image_text="도 박 중 독 · 무 료 상 담 센 터")
    assert codes(c) == {("AD-BRD-01", "public_interest")}


def test_spacing_out_ordinary_words_does_not_create_a_claim():
    assert not codes(mk("오 늘 의 날 씨 가 좋 다", "치 킨 이 맛 있 다", category="supplement"))


def test_quote_of_a_letter_spaced_claim_is_the_original_text():
    c = mk("건강", "꾸준히 드시면 치 매 예 방에 도움이 됩니다.", category="supplement")
    assert _quotes(c, "AD-HLT-01") == [("body", "치 매 예 방")]


# --------------------------------------------------------------------------- F-10 분해형(NFD) 한글 자모
@pytest.mark.parametrize("phrase", ["선입금", "무조건 승인", "수익 보장"])
def test_decomposed_hangul_is_composed_and_the_quote_is_the_original_slice(phrase):
    nfd = unicodedata.normalize("NFD", phrase)
    assert nfd != phrase
    c = mk("대출 안내" if "선" in phrase else nfd, nfd if "선" in phrase else "안내", category="invest", disclosure=INV_OK)
    rule = "AD-FRD-01" if "선" in phrase else "AD-GUA-01"
    found = _quotes(c, rule)
    assert found, f"{phrase} (NFD) 를 못 찾음"
    for field, quote in found:
        assert quote in c.text(field)
        assert unicodedata.normalize("NFC", quote) == phrase


def test_composition_keeps_spans_inside_the_original():
    text = unicodedata.normalize("NFD", "보증료 선입금 30만원")
    n = build(text)
    a = n.tight.index("선입금")
    s, e = n.orig_span_from_tight(a, a + 3)
    assert unicodedata.normalize("NFC", text[s:e]) == "선입금" and len(text[s:e]) == len(unicodedata.normalize("NFD", "선입금"))


# --------------------------------------------------------------------------- F-11 한글 수사 순위
@pytest.mark.parametrize("text", ["국내 일위 영어 앱", "업계 일위 브랜드", "고객 만족도 일위", "판매 일위 달성", "시장 점유율 일위"])
def test_hangul_numeral_rank_with_a_ranking_context_is_flagged(text):
    assert any(rule == "AD-ABS-01" for rule, _ in codes(mk(text)))


@pytest.mark.parametrize("text", ["이 일 위에서 만나요", "일위 선수에게 박수를", "오늘의 일위치 안내"])
def test_hangul_numeral_without_a_ranking_context_is_not_flagged(text):
    assert not any(rule == "AD-ABS-01" for rule, _ in codes(mk(text)))


def test_hangul_numeral_rank_with_a_stated_basis_is_review_grade():
    c = mk("국내 일위 영어 앱 (2026년 가상리서치 조사 기준)")
    assert ("AD-ABS-01", "substantiated_needs_check") in codes(c)


# --------------------------------------------------------------------------- F-12 사기 유도: 금액·어구가 낀 표현, 경고 문구
@pytest.mark.parametrize(
    "text",
    [
        "한도 확정을 위해 보증료 20만원을 먼저 입금해 주세요", "수수료 5만원 미리 송금 후 대출 실행", "이자 먼저 납부하면 승인",
        "대출 실행 전 보증료 선입금 30만원이 필요합니다", "인증번호 6자리를 상담원에게 알려 주세요", "OTP 번호를 문자로 보내 주세요",
        "비밀번호와 보안카드 번호를 입력해 주세요",
    ],
)
def test_advance_fee_and_credential_requests_with_words_in_between_are_flagged(text):
    assert ("AD-FRD-01", "deceptive_solicitation") in codes(mk("대출 안내", text, category="general"))


@pytest.mark.parametrize(
    "text",
    [
        "당사는 선입금·선수수료·보증료나 인증번호·OTP를 요구하지 않으며, 이를 요구하면 사기이니 즉시 신고하세요.",
        "선입금을 요구하지 않습니다", "수수료는 미리 안내해 드립니다", "인증번호를 알려 달라고 하지 않습니다",
        "보증료를 먼저 입금하지 않아도 됩니다",
    ],
)
def test_fraud_warnings_and_fee_notices_are_not_flagged(text):
    assert not any(rule == "AD-FRD-01" for rule, _ in codes(mk("대출 안내", disclosure=text, category="general")))


@pytest.mark.parametrize(
    "text",
    [
        "보증료를 먼저 입금하세요. 개인정보는 요구하지 않습니다.",           # 요구 부정이 다른 문장
        "보증료를 먼저 입금하세요 인증번호는 요구하지 않습니다",             # 같은 절이어도 명령형이 먼저 나옴
        "선입금 30만원이 필요합니다 개인정보는 요구하지 않습니다",             # '필요' 표지가 먼저 나옴
    ],
)
def test_a_fraud_warning_cannot_be_used_as_cover_for_a_real_request(text):
    assert ("AD-FRD-01", "deceptive_solicitation") in codes(mk("대출 안내", text, category="general"))


# --------------------------------------------------------------------------- F-13 과거 수익 표현: 수익률이라는 단어 없이 '최근 3년 연 6.2%'
def _fin_missing(c):
    return [f.missing for f in review(c, ReviewConfig(challenge=False)).findings if f.rule_id == "AD-FIN-01"]


@pytest.mark.parametrize("text", ["최근 3년 연 6.2% 채권 투자", "지난 5년 연평균 연 4.1%", "과거 12개월 연 7%"])
def test_past_return_without_the_word_yield_needs_the_past_performance_notice(text):
    assert _fin_missing(mk(text, category="invest", disclosure=INV_OK)) == [["past_performance_notice"]]


def test_past_performance_notice_satisfies_it():
    ok = INV_OK + " 과거 성과가 미래 수익을 보장하지 않습니다."
    assert _fin_missing(mk("최근 3년 연 6.2% 채권 투자", category="invest", disclosure=ok)) == []


def test_an_expected_rate_without_a_past_period_does_not_need_the_notice():
    assert _fin_missing(mk("연 3.5% 적금 연계 상품", category="invest", disclosure=INV_OK)) == []
