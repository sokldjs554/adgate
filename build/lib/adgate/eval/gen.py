"""시드 고정 합성 생성기(in-grammar 대조군).

생성기와 탐지기가 **같은 어휘를 공유**하므로 여기서 나오는 수치는 낙관적이다. 헤드라인 수치로 쓰지 않고,
손으로 쓴 `test`·독립 에이전트가 쓴 `redteam` 과의 *격차*를 보여 주는 용도로만 쓴다.
정답 라벨은 구성 방식으로 정해진다(어떤 표현을 어느 필드에 넣었는가).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..models import Advertiser, Creative
from .dataset import Example, Gold, GoldViolation

LOAN_DISC = "과도한 대출은 개인신용평점 하락의 원인이 될 수 있습니다. 금리는 연 6.5% ~ 15.9%이며 신용도에 따라 달라질 수 있습니다."
INV_DISC = "투자 상품은 원금 손실이 발생할 수 있으며 손실은 투자자에게 귀속됩니다."

# 위반 표현이 하나도 없는 중립 문구(headline, body, cta)
NEUTRAL: dict[str, list[tuple[str, str, str]]] = {
    "loan": [
        ("직장인 생활자금 대출", "모바일로 한도를 먼저 확인해 보세요.", "한도 조회"),
        ("전월세 보증금 대출 안내", "서류는 앱에서 간편하게 제출할 수 있어요.", "신청하기"),
        ("소액 비상금 대출", "필요한 만큼만 빌리고 편하게 상환하세요.", "내 조건 보기"),
    ],
    "invest": [
        ("소액으로 시작하는 분산 투자", "월 적립식으로 부담 없이 시작할 수 있습니다.", "상품 보기"),
        ("해외 ETF 투자 가이드", "투자 전 설명서를 꼭 확인하세요.", "설명서 보기"),
        ("연금 준비는 일찍 시작하세요", "세액공제 혜택을 함께 확인해 보세요.", "자세히 보기"),
    ],
    "supplement": [
        ("매일 챙기는 비타민", "균형 잡힌 식단과 함께 섭취하세요.", "구매하기"),
        ("장 건강에 도움을 주는 유산균", "하루 한 포로 간편하게 챙기세요.", "구매하기"),
        ("혈행 개선에 도움을 줄 수 있는 오메가-3", "기능성 원료를 담았습니다.", "상세 보기"),
    ],
    "cosmetic": [
        ("건조한 계절 보습 크림", "피부 장벽 관리를 도와줍니다.", "구매하기"),
        ("가볍게 발리는 선크림", "백탁 없이 산뜻하게 마무리됩니다.", "구매하기"),
        ("촉촉한 수분 에센스", "가벼운 사용감의 에센스입니다.", "구매하기"),
    ],
    "ecommerce": [
        ("겨울 이불 세트", "부드러운 촉감의 기획 상품입니다.", "쇼핑하기"),
        ("접이식 캠핑 의자", "무게 1.2kg, 가방에 쏙 들어가요.", "구매하기"),
        ("무선 이어폰 신제품", "생활 방수를 지원합니다.", "제품 보기"),
    ],
    "app": [
        ("가계부 앱, 영수증은 사진 한 장으로", "찍으면 항목이 자동으로 분류돼요.", "앱 다운로드"),
        ("일정 관리 앱", "할 일을 한곳에서 정리해 보세요.", "시작하기"),
        ("메모 동기화 서비스", "기기 간에 메모를 바로 이어서 써요.", "다운로드"),
    ],
    "education": [
        ("퇴근 후 데이터 분석 입문", "파이썬 기초부터 차근차근 배워요.", "무료 체험"),
        ("초등 코딩 방학 특강", "블록 코딩으로 게임을 만들어 봅니다.", "신청하기"),
        ("토익 단기 완성반", "라이브 수업과 단어 퀴즈를 함께 진행해요.", "상담 신청"),
    ],
    "general": [
        ("주말 도자기 원데이 클래스", "처음 만져 보는 분도 편하게 참여하세요.", "예약하기"),
        ("동네 책방 북토크 안내", "이번 주 토요일 오후에 열립니다.", "참가 신청"),
        ("이사 견적을 한 번에 받아 보세요", "방문 견적은 무료입니다.", "견적 받기"),
    ],
}
NAMES = {
    "loan": "생성캐피탈", "invest": "생성자산운용", "supplement": "생성헬스", "cosmetic": "생성코스메틱",
    "ecommerce": "생성마켓", "app": "생성앱스", "education": "생성아카데미", "general": "생성스토어",
    "adult": "생성성인몰", "gambling": "생성베팅랩",
}


@dataclass(frozen=True)
class Snippet:
    rule_id: str
    phrase: str
    sev: str                                  # reject | review
    categories: tuple[str, ...] | None = None  # None: 모든 업종
    fields: tuple[str, ...] = ("headline", "body")


_ALL = None
VIOLATIONS: tuple[Snippet, ...] = (
    *(Snippet("AD-GUA-01", p, "reject") for p in (
        "무조건 승인", "100% 승인", "수익 보장", "원금 보장", "확정 수익", "손실 없는 투자", "한 달 만에 10kg 감량", "3개월 만에 2배 수익")),
    *(Snippet("AD-ABS-01", p, "reject") for p in ("최고의 품질", "국내 1위", "업계 최초", "유일한 선택", "완벽한 보호", "100% 만족", "No.1")),
    Snippet("AD-ABS-01", "판매량 1위 (2026년 ○○리서치 조사 기준)", "review"),
    *(Snippet("AD-HLT-01", p, "reject", ("supplement", "cosmetic", "ecommerce", "general")) for p in (
        "암 예방", "당뇨 개선", "관절염 완치", "아토피 치료", "치매 예방", "탈모증 치료")),
    *(Snippet("AD-CMP-01", p, "review") for p in ("타사 대비 2배 빠름", "다른 곳보다 저렴해요", "경쟁사보다 정확한")),
    *(Snippet("AD-CMP-01", p, "reject") for p in ("다른 곳은 바가지", "타사는 사기")),
    *(Snippet("AD-URG-01", p, "review") for p in ("오늘만", "마감 임박", "선착순 50명", "한정 수량", "지금 아니면 못 사요", "마지막 기회")),
    *(Snippet("AD-ENDO-01", p, "review") for p in ("의사들이 추천하는", "전문가 인증", "임상 입증된", "후기 10만 건", "만족도 98%")),
    *(Snippet("AD-BRD-01", p, "reject") for p in ("해외 카지노 사이트", "토토 사이트", "마약 판매")),
    *(Snippet("AD-BRD-01", p, "review") for p in ("도박 중독 상담", "마약 중독 치료·재활 상담")),
    *(Snippet("AD-FRD-01", p, "reject") for p in ("선입금 후 송금", "인증번호를 알려주세요", "신용 무관", "무심사 대출", "원격제어 앱 설치")),
)
LOOKALIKES: tuple[str, ...] = (
    "수익을 보장하지 않습니다", "원금 비보장 상품입니다", "100% 환불 보장", "교환·환불 100% 보장",
    "본 제품은 질병의 예방 및 치료를 위한 의약품이 아닙니다", "당뇨 환자는 섭취 전 의사와 상담하세요",
    "성인 금연 캠페인에 참여하세요", "인증번호는 절대 타인에게 알려주지 마세요", "다른 사람과 함께해요",
    "최고 연 15.9%", "최고 50% 할인", "최저 월 9,900원",
)
LOOKALIKE_LANDING = "금리 연 6.5% ~ 15.9%. 최대 50% 할인. 월 9,900원부터. 월 19,900원. 미개봉 상품은 100% 환불됩니다."


def _evade(phrase: str, rng: random.Random) -> tuple[str, str]:
    """위반 표현을 우회 표기로 바꾼다. (변환된 표현, 변환 이름)"""
    kind = rng.choice(["spaced", "dotted", "zerowidth", "fullwidth"])
    chars = list(phrase)
    if kind == "spaced":
        return " ".join(c for c in phrase if c != " "), kind
    if kind == "dotted":
        return ".".join(c for c in phrase if c != " "), kind
    if kind == "zerowidth":
        return "​".join(chars), kind
    table = str.maketrans("0123456789%", "０１２３４５６７８９％")
    out = phrase.translate(table)
    return out, kind


def _creative_base(rng: random.Random, category: str, idx: int) -> dict[str, object]:
    h, b, c = rng.choice(NEUTRAL[category])
    disc = LOAN_DISC if category == "loan" else INV_DISC if category == "invest" else None
    return {
        "ad_id": f"g-{idx:05d}", "headline": h, "body": b, "cta": c, "disclosure": disc,
        "image_text": None, "landing_text": None,
    }


def _place(base: dict[str, object], phrase: str, field: str) -> str:
    """phrase 를 field 에 넣고 그 필드의 새 값을 돌려준다(원문 그대로 포함)."""
    cur = str(base.get(field) or "")
    if field == "headline":
        base[field] = f"{phrase}, {cur}"
    elif field == "body":
        base[field] = f"{cur} {phrase}."
    elif field == "image_text":
        base[field] = phrase
    elif field == "disclosure":
        base[field] = f"{cur} {phrase}.".strip()
    else:
        base[field] = f"{phrase} {cur}".strip()
    return str(base[field])


class _Builder:
    """한 건을 만드는 동안의 상태(본문, 정답 위반, 등급, 태그)."""

    def __init__(self, rng: random.Random, category: str, idx: int) -> None:
        self.rng = rng
        self.category = category
        self.base = _creative_base(rng, category, idx)
        self.verified = True
        self.gold: list[GoldViolation] = []
        self.sevs: list[str] = []
        self.tags: list[str] = []

    def pool(self, evasion: bool = False) -> list[Snippet]:
        ok = [s for s in VIOLATIONS if s.categories is None or self.category in s.categories]
        if evasion:
            ok = [s for s in ok if s.rule_id in ("AD-GUA-01", "AD-FRD-01", "AD-ABS-01") and s.sev == "reject"]
        return ok

    def add(self, snip: Snippet, evade: bool = False) -> None:
        phrase = snip.phrase
        if evade:
            phrase, kind = _evade(phrase, self.rng)
            self.tags.append(kind)
        field = self.rng.choice(snip.fields)
        _place(self.base, phrase, field)
        self.gold.append(GoldViolation(rule_id=snip.rule_id, field=field, quote=phrase))
        self.sevs.append(snip.sev)

    def add_meta(self, rule_id: str, sev: str, *, missing: list[str] | None = None) -> None:
        self.gold.append(GoldViolation(rule_id=rule_id, missing=missing))
        self.sevs.append(sev)

    def metadata(self) -> None:
        rng, base = self.rng, self.base
        kind = rng.choice(["adv", "fin", "brd", "lnd_mismatch", "lnd_unverifiable"])
        finance = self.category in ("loan", "invest")
        if kind == "adv" and finance:
            self.verified = False
            self.add_meta("AD-ADV-01", "reject")
        elif kind == "fin" and finance:
            base["disclosure"] = None
            self.add_meta("AD-FIN-01", "reject", missing=["credit_warning", "rate_info"] if self.category == "loan" else ["principal_loss_warning"])
        elif kind == "brd":
            self.category = rng.choice(["adult", "gambling"])
            self.add_meta("AD-BRD-01", "reject")
        elif kind in ("lnd_mismatch", "lnd_unverifiable"):
            price = rng.choice([3900, 4900, 9900, 14900])
            phrase = f"월 {price:,}원"
            base["headline"] = f"{base['headline']} {phrase}"
            base["landing_text"] = f"요금 안내: 월 {price + 10000:,}원." if kind == "lnd_mismatch" else "요금은 상담 후 안내합니다."
            self.gold.append(GoldViolation(rule_id="AD-LND-01", field="headline", quote=phrase))
            self.sevs.append("reject" if kind == "lnd_mismatch" else "review")
        else:
            kind = "clean_fallback"
        self.tags.append(kind)

    def build(self) -> Example:
        base = self.base
        decision = "REJECT" if "reject" in self.sevs else "REVIEW" if "review" in self.sevs else "APPROVE"

        def opt(key: str) -> str | None:
            value = base[key]
            return str(value) if value else None

        creative = Creative(
            ad_id=str(base["ad_id"]),
            advertiser=Advertiser(name=NAMES[self.category], category=self.category, verified=self.verified),
            headline=str(base["headline"]), body=str(base["body"]), cta=str(base["cta"]),
            disclosure=opt("disclosure"), image_text=opt("image_text"), landing_text=opt("landing_text"),
        )
        idx = str(base["ad_id"]).split("-")[1]
        return Example(id=f"G-{idx[1:]}", creative=creative, gold=Gold(decision=decision, violations=self.gold, tags=self.tags))


def generate(n: int = 600, seed: int = 7) -> list[Example]:
    rng = random.Random(seed)
    categories = ["loan", "invest", "supplement", "cosmetic", "ecommerce", "app", "education", "general"]
    out: list[Example] = []
    for i in range(n):
        plan = rng.choices(["clean", "lookalike", "single", "multi", "evasion", "metadata"], weights=[22, 18, 30, 10, 10, 10])[0]
        b = _Builder(rng, rng.choice(categories), i)
        b.tags.append(plan)
        if plan == "lookalike":
            _place(b.base, rng.choice(LOOKALIKES), "body")
            b.base["landing_text"] = LOOKALIKE_LANDING
        elif plan == "single":
            b.add(rng.choice(b.pool()))
        elif plan == "multi":
            first, second = rng.sample(b.pool(), 2)
            while first.rule_id == second.rule_id:
                first, second = rng.sample(b.pool(), 2)
            b.add(first)
            b.add(second)
        elif plan == "evasion":
            b.add(rng.choice(b.pool(evasion=True)), evade=True)
        elif plan == "metadata":
            b.metadata()
        out.append(b.build())
    return out
