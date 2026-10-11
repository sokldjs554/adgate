"""독립 리뷰(docs/REVIEW.md)가 *실행해서 확인한* 오탐·미탐 사례. 같은 부류의 변형은 test_redteam_regressions.py 와 같은 방식으로 여럿 둔다.

각 항목은 (식별자, 소재 인자, 기대 판정)이다. 기대 판정은 정책 문서(POLICY.md)와 리뷰어의 '기대' 를 그대로 옮겼다.
`-ok` 가 붙은 항목은 *정상 유사* — 고친 규칙이 정상 소재를 건드리지 않는지 확인하는 음성 대조다.
"""

from __future__ import annotations

import pytest
from conftest import mk

from adgate.config import ReviewConfig
from adgate.pipeline import review

INV = "투자 상품은 원금 손실이 발생할 수 있습니다."
LOAN = "과도한 대출은 개인신용평점 하락의 원인이 될 수 있습니다. 금리는 신용도에 따라 달라질 수 있습니다."
DISCLOSURE = {"INV": INV, "LOAN": LOAN}

CASES = [
    ('H1', {"headline": '당뇨 완치, 의약품이 아닙니다', "category": 'supplement'}, 'REJECT'),
    ('H1', {"headline": '이 제품은 의약품이 아니며 암을 치료합니다', "category": 'supplement'}, 'REJECT'),
    ('H1', {"headline": '암 완치 사례, 과장이 아닙니다', "category": 'supplement'}, 'REJECT'),
    ('H1', {"headline": '당뇨 치료 효과, 거짓이 아닙니다', "category": 'supplement'}, 'REJECT'),
    ('H1-ok', {"headline": '본 제품은 질병의 예방 및 치료를 위한 의약품이 아닙니다.', "category": 'supplement'}, 'APPROVE'),
    ('H1-ok', {"headline": '본 제품은 당뇨 예방 및 치료를 위한 의약품이 아닙니다.', "category": 'supplement'}, 'APPROVE'),
    ('H1-ok', {"headline": '암을 치료하지 않습니다', "category": 'supplement'}, 'APPROVE'),
    ('H2', {"headline": '난소암 완치', "category": 'supplement'}, 'REJECT'),
    ('H2', {"headline": '직장암 예방', "category": 'supplement'}, 'REJECT'),
    ('H2', {"headline": '백혈병 완치', "category": 'supplement'}, 'REJECT'),
    ('H2', {"headline": '독감 예방', "category": 'supplement'}, 'REJECT'),
    ('H2', {"headline": '감기 예방', "category": 'supplement'}, 'REJECT'),
    ('H2', {"headline": '비만 치료', "category": 'supplement'}, 'REJECT'),
    ('H2', {"headline": '대상포진 예방', "category": 'supplement'}, 'REJECT'),
    ('H2', {"headline": 'ADHD 치료', "category": 'supplement'}, 'REJECT'),
    ('H2 GUA', {"headline": '1년 만에 2배 수익', "disclosure": INV, "category": 'invest'}, 'REJECT'),
    ('H2 GUA', {"headline": '일주일 만에 3kg 감량', "category": 'supplement'}, 'REJECT'),
    ('H2 GUA', {"headline": '한 달 만에 10키로 감량', "category": 'supplement'}, 'REJECT'),
    ('H2 GUA', {"headline": '하루 만에 3kg 빠져요', "category": 'supplement'}, 'REJECT'),
    ('H2 FRD', {"headline": '수수료를 먼저 보내주시면 대출 실행'}, 'REJECT'),
    ('H3', {"headline": '원금 보장 걱정 없습니다', "disclosure": INV, "category": 'invest'}, 'REJECT'),
    ('H3', {"headline": '수익 보장 걱정 없음', "disclosure": INV, "category": 'invest'}, 'REJECT'),
    ('H3', {"headline": '수익 보장 문제 없어요', "disclosure": INV, "category": 'invest'}, 'REJECT'),
    ('H3', {"headline": '무조건 승인 거절 없음', "disclosure": LOAN, "category": 'loan'}, 'REJECT'),
    ('H3-ok', {"headline": '수익 보장이 없습니다', "disclosure": INV, "category": 'invest'}, 'APPROVE'),
    ('H4', {"headline": '품질 인증 대출 100% 승인', "disclosure": LOAN, "category": 'loan'}, 'REJECT'),
    ('H4', {"headline": '정품 대출 100% 승인', "disclosure": LOAN, "category": 'loan'}, 'REJECT'),
    ('H4', {"headline": '배송 빠른 대출 100% 승인', "disclosure": LOAN, "category": 'loan'}, 'REJECT'),
    ('H4', {"headline": '가격 비교 없이 대출 100% 승인', "disclosure": LOAN, "category": 'loan'}, 'REJECT'),
    ('H4-ok', {"headline": '환불 100% 보장'}, 'APPROVE'),
    ('H4-ok', {"headline": '교환·환불 100% 보장'}, 'APPROVE'),
    ('H4-ok', {"headline": '품질 100% 보장'}, 'APPROVE'),
    ('H5', {"headline": '원금 손실 가능성이 없는 안전 투자', "category": 'invest'}, 'REJECT'),
    ('H5', {"headline": '원금 손실 걱정 없는 투자', "category": 'invest'}, 'REJECT'),
    ('H5', {"headline": '손실이 발생하지 않습니다', "category": 'invest'}, 'REJECT'),
    ('H5', {"headline": '신용점수 하락 걱정 없이 조회하세요 연 4.5%', "category": 'loan'}, 'REJECT'),
    ('H5', {"headline": '신용점수에 영향 없음 연 4.5%', "category": 'loan'}, 'REJECT'),
    ('M2', {"headline": '최고 연구진이 만든 제품'}, 'REJECT'),
    ('M2', {"headline": '최고 연예인이 선택한 브랜드'}, 'REJECT'),
    ('M2', {"headline": '최고 월드클래스 품질'}, 'REJECT'),
    ('M2', {"headline": '최고 1등급 원단'}, 'REJECT'),
    ('M2', {"headline": '최고 5성급 서비스'}, 'REJECT'),
    ('M2-ok', {"headline": '최고 연 5.9%'}, 'APPROVE'),
    ('M2-ok', {"headline": '최고 월 19,900원'}, 'APPROVE'),
    ('M2-ok', {"headline": '최고 한도 5천만원'}, 'APPROVE'),
    ('M2-ok', {"headline": '최고 50% 할인'}, 'APPROVE'),
    ('M2-ok', {"headline": '최저 월 9,900원'}, 'APPROVE'),
    ('M3', {"headline": '카지노 가입 쿠폰 지원'}, 'REJECT'),
    ('M3', {"headline": '마약 구매 상담'}, 'REJECT'),
    ('M3', {"headline": '총기 판매 지원'}, 'REJECT'),
    ('M3', {"headline": '바카라 필승 교육 신청'}, 'REJECT'),
    ('M3-ok', {"headline": '도박 중독 상담 센터'}, 'REVIEW'),
    ('M5', {"headline": '1일 1회 섭취', "landing": '구매 후 30일 이내 환불 가능', "category": 'supplement'}, 'APPROVE'),
    ('M6a', {"headline": '총 기능 20가지를 한 앱에'}, 'APPROVE'),
    ('M6a', {"headline": '환불 이유 일부 설명 페이지'}, 'APPROVE'),
    ('M6a', {"headline": 'Step 1 위치 확인하기'}, 'APPROVE'),
    ('M6a', {"headline": '오늘 만나는 새로운 친구'}, 'APPROVE'),
    ('M6b', {"headline": '수익을 보장해 드리지 않습니다', "disclosure": INV, "category": 'invest'}, 'APPROVE'),
    ('M6b', {"headline": '수익 보장이 아니라 변동 상품입니다', "disclosure": INV, "category": 'invest'}, 'APPROVE'),
    ('M6b', {"headline": '원금 보장 여부를 확인하세요', "disclosure": INV, "category": 'invest'}, 'APPROVE'),
    ('M6c', {"headline": '손해 없이 환불해 드립니다'}, 'APPROVE'),
    ('M6c', {"headline": '위험 없이 체험해 보세요'}, 'APPROVE'),
    ('M6c', {"headline": '누드 베이지 립스틱', "category": 'cosmetic'}, 'APPROVE'),
    ('M6c', {"headline": '최고경영자 과정 모집', "category": 'education'}, 'APPROVE'),
    ('M6c', {"headline": '최저임금 준수 알바 매칭'}, 'APPROVE'),
    ('L1 URG', {"headline": '오늘만 특가', "landing": '무기한 무료 이용 가능'}, 'REVIEW'),
    ('L1 URG', {"headline": '오늘만 특가', "landing": '환불 기간 안내: 구매 후 7일'}, 'REVIEW'),
    ('L1 URG', {"headline": '선착순 100명', "landing": '누적 후기 100명'}, 'REVIEW'),
    ('L1 URG-ok', {"headline": '오늘만 특가', "landing": '10월 31일까지 진행'}, 'APPROVE'),
    ('L1 URG-ok', {"headline": '선착순 100명', "landing": '선착순 100명 한정 수량'}, 'APPROVE'),
    ('L1 ABS', {"headline": '최고의 품질 평가 20000건'}, 'REJECT'),
    ('L1 ABS', {"headline": '국내 1위 *', "disclosure": '* 2026년 ○○리서치 조사 기준'}, 'REVIEW'),
]


def outcome(kwargs):
    return review(mk(**kwargs), ReviewConfig(challenge=False)).outcome


@pytest.mark.parametrize(("tag", "kwargs", "expected"), CASES, ids=[f"{t}:{k['headline'][:18]}" for t, k, _ in CASES])
def test_reviewer_reproduced_cases(tag, kwargs, expected):
    assert outcome(kwargs) == expected, f"{tag} {kwargs}"
