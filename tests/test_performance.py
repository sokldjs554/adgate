"""최악 입력에서도 심사가 선형에 가깝게 끝나는가.

독립 리뷰(docs/REVIEW.md C1·C2)가 정규식 하나로 서버 전체를 수십 초 멈추게 하는 입력을 찾았다. 파이썬 `re` 는 GIL 을 놓지 않으므로
요청 하나가 프로세스 전체를 막는다. 그래서 *절대 시간*(느린 CI 에서도 넉넉한 한도)과 *늘어나는 정도*(입력을 4배로 했을 때 시간이 몇 배가 되는가)를 둘 다 본다.
"""

from __future__ import annotations

import time

import pytest
from conftest import mk

from adgate.config import ReviewConfig
from adgate.normalize import build
from adgate.numbers import extract
from adgate.pipeline import review

AD = 2000
LANDING = 20000

# (이름, 한 조각) — 필요한 길이만큼 반복해서 쓴다.
PIECES = {
    "숫자만": "1",
    "숫자와 공백": "1 ",
    "쉼표 숫자": "1,",
    "마침표 숫자": "9.",
    "단위만 반복": "원",
    "숫자+단위 반복": "1만",
    "퍼센트 반복": "1%",
    "기간 반복": "1일 ",
    "질병 반복(두 글자)": "당뇨 ",
    "질병 반복(한 글자)": "암 ",
    "질병+치료": "암 예방 ",
    "낱글자+공백": "가 ",
    "낱글자 끝이 안 맞는 덩어리": "가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 가 나다 ",
    "구분 기호": ". ",
    "하이픈 열": "-",
    "공백": " ",
    "줄바꿈": "\n",
    "채움 문자": "ㅤ",
    "제로폭": "​",
    "장식 기호": "★",
    "괄호": "()",
    "한자": "保障",
    "보장 구절": "수익보장 ",
    "분해형 자모": "ㅅ",
    "금액 조각": "3만 9천 ",
    "신용 문구": "신용점수 하락 연 5% ",
    # 이미지 셋 수정(F-31)이 더한 문형: 매치마다 문장 첫머리를 확인하거나 경고 문형을 살피는 경로
    "범위 설명 각주": "최고 할인율은 일부 모델에 한하며 ",
    "원격 설치 경고": "원격제어 앱을 설치하지 마세요 ",
    "원격 설치 조사": "원격제어 앱을 설치 ",
    "인증번호 회신": "인증번호를 회신 ",
    "요구 거절": "선입금 요구에는 응하지 마세요 ",
}


def best_of(fn, arg, runs: int = 3) -> float:
    """가장 빠른 실행 시간. 다른 테스트의 가비지 수집·CPU 경쟁이 한 번 끼어도 선형성 판정이 흔들리지 않게 한다."""
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        fn(arg)
        best = min(best, time.perf_counter() - t0)
    return best


def repeat(piece: str, n: int) -> str:
    return (piece * (n // len(piece) + 1))[:n]


def creative(piece: str, *, ad: int = AD, landing: int = LANDING, category: str = "supplement"):
    text = repeat(piece, ad)
    return mk(text, text, text, disclosure=text, image_text=text, landing=repeat(piece, landing), category=category)


@pytest.mark.parametrize("name", list(PIECES))
def test_full_length_fields_finish_within_the_budget(name):
    c = creative(PIECES[name])
    t0 = time.perf_counter()
    review(c)
    assert time.perf_counter() - t0 < 4.0, name


@pytest.mark.parametrize("category", ["loan", "invest", "gambling"])
def test_finance_and_restricted_categories_are_also_bounded(category):
    c = creative("신용점수 하락 연 5% 원금 손실 ", category=category)
    t0 = time.perf_counter()
    review(c)
    assert time.perf_counter() - t0 < 4.0


@pytest.mark.parametrize("name", ["숫자만", "숫자와 공백", "숫자+단위 반복", "퍼센트 반복", "기간 반복", "단위만 반복", "쉼표 숫자"])
def test_number_extraction_is_linear(name):
    piece = PIECES[name]
    small, large = repeat(piece, 4000), repeat(piece, 16000)
    t_small, t_large = max(best_of(extract, small), 2e-4), best_of(extract, large)
    assert t_large < 1.0 and t_large / t_small < 16, f"{name}: {t_small:.4f}s → {t_large:.4f}s"


@pytest.mark.parametrize("name", ["질병 반복(두 글자)", "낱글자+공백", "낱글자 끝이 안 맞는 덩어리", "구분 기호", "공백", "줄바꿈", "장식 기호", "괄호", "채움 문자"])
def test_normalization_is_linear(name):
    piece = PIECES[name]
    small, large = repeat(piece, 4000), repeat(piece, 16000)
    t_small, t_large = max(best_of(build, small), 2e-4), best_of(build, large)
    assert t_large < 1.0 and t_large / t_small < 16, f"{name}: {t_small:.4f}s → {t_large:.4f}s"


def test_a_26_digit_number_does_not_stall_the_landing_comparison():
    """리뷰 C1: 숫자 26자리 필드가 있으면 금액 정규식이 34초 걸렸다."""
    t0 = time.perf_counter()
    review(mk("1" * 26, "1" * 26, landing="가격 " + "1" * 26), ReviewConfig())
    assert time.perf_counter() - t0 < 1.0


# --------------------------------------------------------------------------- 발견 수에 대한 시간 (CI 의 파이썬 3.11 이 잡은 결함)
def _naive_resolve(findings, policy):
    """수정 전 구현(남긴 것 전부와 견주는 2차 시간). 새 구현이 같은 결과를 내는지의 기준이다."""
    spanned = [f for f in findings if f.start is not None and f.end is not None]
    others = [f for f in findings if f.start is None or f.end is None]
    spanned.sort(key=lambda f: (policy.rank(f.rule_id), -((f.end or 0) - (f.start or 0)), f.start or 0))
    kept = []
    for f in spanned:
        if not any(k.field == f.field and f.start < k.end and k.start < f.end for k in kept):
            kept.append(f)
    kept.sort(key=lambda f: (f.field or "", f.start or 0))
    return kept + others


def _finding(rule_id, field, start, end):
    from adgate.models import Finding

    return Finding(rule_id=rule_id, code="x", severity="review", field=field, quote="q", start=start, end=end)


def test_resolve_overlaps_matches_the_naive_version_on_random_findings_including_empty_spans():
    import random

    from adgate.policy import load_policy
    from adgate.rules import resolve_overlaps

    policy = load_policy()
    rules = ["AD-GUA-01", "AD-HLT-01", "AD-ABS-01", "AD-CMP-01", "AD-URG-01"]
    rng = random.Random(20261012)
    for _ in range(400):
        fs = []
        for _ in range(rng.randint(0, 40)):
            s = rng.randint(0, 30)
            fs.append(_finding(rng.choice(rules), rng.choice(["headline", "body", None]), s, s + rng.choice([0, 0, 1, 2, 3, 5, 9])))
        fs.append(_finding("AD-FIN-01", None, 0, 0))
        got = [(f.rule_id, f.field, f.start, f.end) for f in resolve_overlaps(list(fs), policy)]
        want = [(f.rule_id, f.field, f.start, f.end) for f in _naive_resolve(list(fs), policy)]
        assert got == want


def test_resolve_overlaps_handles_twenty_thousand_findings_quickly():
    """발견 수에 대해 선형에 가까워야 한다. 2차 시간이면 이 입력에 수십 초가 걸린다."""
    from adgate.policy import load_policy
    from adgate.rules import resolve_overlaps

    policy = load_policy()
    fs = [_finding("AD-GUA-01", "body", i * 3, i * 3 + 2) for i in range(20000)]
    t0 = time.perf_counter()
    kept = resolve_overlaps(fs, policy)
    assert len(kept) == 20000 and time.perf_counter() - t0 < 1.0
