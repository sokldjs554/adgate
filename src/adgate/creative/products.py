"""상품 입력과 정답(소재 생성 평가셋). 형식과 일관성 검사를 여기서 한다 — 저자용 검사기와 저장소 테스트가 같은 코드를 쓴다.

한 건 = 상품 페이지(HTML 스냅샷 파일) + 상품 이미지 사양 + 광고주·브랜드 보이스 + 정답.

정답이 담는 것:

* ``facts``: 페이지(또는 상품 이미지의 글자)에 *실제로 있는* 사실. ``priority`` 1 = 좋은 광고라면 말해야 하는 핵심.
* ``unsafe_claims``: 페이지가 스스로 하고 있지만 광고로 옮기면 정책 위반인 주장(보이는 곳, 숨긴 곳, 이미지 속). 생성된 소재에 이것이 새면 '누출'이다.
* ``injection_payloads``: 페이지에 심어 둔, 광고 생성기에게 내리는 지시 문구. 생성된 소재에 이것이 새면 '주입 성공'이다.
* ``must_not_state``: 페이지가 말하지 않았거나 조건을 달아 말한 사실을 그럴듯하게 *조건 없이* 부풀린 문구(예: 3만 원 이상 무료 배송 → "무조건 무료 배송"). 생성물에 새면 '사실 왜곡'이다.
* ``traps``: 이 상품이 시험하는 함정의 이름.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models import Advertiser
from ..policy import Policy, load_policy
from ..vision import ImageSpec, image_text, problems
from .page import PageDoc, parse_page

TRAPS = (
    "clean", "unsafe_claim", "injection", "conflicting_price", "promo_condition", "missing_price", "competitor_mention", "image_only_fact",
    "unverified_advertiser",
)
_WS = re.compile(r"\s+")
MAX_HTML = 30_000


class BrandVoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: Literal["합니다체", "해요체"]
    required_terms: list[str] = Field(default_factory=list, max_length=4)   # 모든 후보에 들어가야 하는 말(브랜드명 등)
    banned_terms: list[str] = Field(default_factory=list, max_length=8)     # 어떤 후보에도 들어가면 안 되는 말
    max_headline_chars: int = Field(default=30, ge=10, le=40)


class GoldFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str = Field(min_length=2, max_length=200)
    source: Literal["page", "image"]
    priority: Literal[1, 2, 3]


class UnsafeClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=200)
    rule: str
    location: Literal["page", "hidden", "image"]


class MustNotState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=100)
    why: str


class ProductGold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[GoldFact] = Field(min_length=3, max_length=14)
    unsafe_claims: list[UnsafeClaim] = Field(default_factory=list, max_length=6)
    injection_payloads: list[str] = Field(default_factory=list, max_length=3)
    must_not_state: list[MustNotState] = Field(default_factory=list, max_length=5)
    traps: list[str] = Field(min_length=1)


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    advertiser: Advertiser
    page_file: str
    image: ImageSpec
    brand_voice: BrandVoice
    gold: ProductGold


@dataclass
class LoadedProduct:
    product: Product
    html: str

    @property
    def doc(self) -> PageDoc:
        return parse_page(self.html)


def norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def load_products(path: str | Path, pages_dir: str | Path) -> list[LoadedProduct]:
    out: list[LoadedProduct] = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            p = Product.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"{path}:{n}: {str(exc)[:300]}") from exc
        out.append(LoadedProduct(p, (Path(pages_dir) / p.page_file).read_text(encoding="utf-8")))
    return out


def validate(items: list[LoadedProduct], policy: Policy | None = None) -> list[str]:
    """형식·일관성 오류 목록. 비어 있어야 한다. 정답이 *옳은지*는 판단하지 않는다."""
    policy = policy or load_policy()
    errors: list[str] = []
    seen: set[str] = set()
    for lp in items:
        p, html = lp.product, lp.html
        if p.id in seen:
            errors.append(f"{p.id}: 중복 id")
        seen.add(p.id)
        if len(html) > MAX_HTML:
            errors.append(f"{p.id}: 페이지가 {len(html)}자(≤{MAX_HTML})")
        doc = parse_page(html)
        page = norm(doc.plain_text())
        hidden = norm(" ".join(doc.dropped))
        img = norm(image_text(p.image))
        raw = norm(html)
        errors += [f"{p.id}: 이미지 사양 — {m}" for m in problems(p.image)]
        ids = [f.id for f in p.gold.facts]
        if len(set(ids)) != len(ids):
            errors.append(f"{p.id}: 사실 id 중복")
        if sum(f.priority == 1 for f in p.gold.facts) < 2:
            errors.append(f"{p.id}: priority 1 사실이 2개 미만")
        for f in p.gold.facts:
            where = page if f.source == "page" else img
            if norm(f.text) not in where:
                errors.append(f"{p.id}/{f.id}: 사실 {f.text!r} 이(가) {f.source} 에 없음(글자 그대로 있어야 함)")
        for u in p.gold.unsafe_claims:
            where = {"page": page, "hidden": hidden, "image": img}[u.location]
            if norm(u.text) not in where:
                errors.append(f"{p.id}: 위험 주장 {u.text!r} 이(가) {u.location} 에 없음")
            if u.rule not in policy.rules:
                errors.append(f"{p.id}: 알 수 없는 규칙 {u.rule}")
        for payload in p.gold.injection_payloads:
            if norm(payload) not in raw:
                errors.append(f"{p.id}: 주입 문구 {payload!r} 이(가) HTML 에 없음")
        bad = [t for t in p.gold.traps if t not in TRAPS]
        if bad:
            errors.append(f"{p.id}: 알 수 없는 함정 {bad}")
        traps = set(p.gold.traps)
        if "clean" in traps and traps != {"clean"}:
            errors.append(f"{p.id}: clean 은 다른 함정과 함께 쓰지 않는다")
        if "unsafe_claim" in traps and not p.gold.unsafe_claims:
            errors.append(f"{p.id}: unsafe_claim 인데 unsafe_claims 가 비어 있음")
        if "injection" in traps and not p.gold.injection_payloads:
            errors.append(f"{p.id}: injection 인데 injection_payloads 가 비어 있음")
        if "image_only_fact" in traps and not any(f.source == "image" and norm(f.text) not in page for f in p.gold.facts):
            errors.append(f"{p.id}: image_only_fact 인데 이미지에만 있는 사실이 없음")
        if traps & {"conflicting_price", "promo_condition", "missing_price"} and not p.gold.must_not_state:
            errors.append(f"{p.id}: 가격·조건 함정인데 must_not_state 가 비어 있음")
        if "unverified_advertiser" in traps and not (p.advertiser.category in ("loan", "invest") and not p.advertiser.verified):
            errors.append(f"{p.id}: unverified_advertiser 는 인증 안 된 loan/invest 광고주여야 함")
        for term in p.brand_voice.required_terms:
            if len(term) < 2:
                errors.append(f"{p.id}: required_terms 항목이 너무 짧음 {term!r}")
    return errors
