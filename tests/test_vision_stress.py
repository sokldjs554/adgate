"""이미지 스트레스(열화·지시문 심기)가 정의대로 결정론이고, 심은 문장이 원본의 정답을 바꾸지 않는지 시험한다(docs/EVAL_VISION2.md)."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops

from adgate.config import ReviewConfig
from adgate.models import Creative
from adgate.pipeline import review
from adgate.vision import ImageSpec, problems, render
from adgate.vision.spec import Canvas, Item
from adgate.vision.stress import (
    BAND_H,
    KINDS,
    LEVELS,
    PHRASES,
    STEALTH,
    WHERE,
    _band_spec,
    band_png,
    degrade,
    inject_twin,
    seed_for,
    twin_id,
    twin_texts,
)


def base_png() -> bytes:
    spec = ImageSpec(canvas=Canvas(w=640, h=480, bg="#f4f1ea"), items=[
        Item(kind="text", box=(5, 5, 90, 20), text="가상 적립식 투자 서비스", bold=True, align="center"),
        Item(kind="button", box=(30, 60, 40, 12), text="시작하기"),
        Item(kind="fine_print", box=(5, 90, 90, 6), text="※ 투자 원금의 일부 또는 전부 손실이 발생할 수 있습니다.", size=11),
    ])
    buf = io.BytesIO()
    render(spec).save(buf, format="PNG")
    return buf.getvalue()


def img(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def mean_abs_diff(a: bytes, b: bytes) -> float:
    d = ImageChops.difference(img(a), img(b)).convert("L")
    hist = d.histogram()
    return sum(i * c for i, c in enumerate(hist)) / sum(hist)


# --------------------------------------------------------------------------- 열화
@pytest.mark.parametrize("kind", KINDS)
def test_degrade_is_deterministic_and_keeps_the_canvas_size(kind):
    png = base_png()
    a, b = degrade(png, kind, 2, seed=7), degrade(png, kind, 2, seed=7)
    assert a == b
    assert img(a).size == img(png).size


@pytest.mark.parametrize("kind", KINDS)
def test_stronger_levels_distort_more(kind):
    png = base_png()
    diffs = [mean_abs_diff(png, degrade(png, kind, lv, seed=3)) for lv in LEVELS]
    assert diffs[0] > 0 and diffs[0] < diffs[1] < diffs[2], diffs


def test_noise_depends_on_the_seed_and_other_kinds_do_not():
    png = base_png()
    assert degrade(png, "noise", 2, seed=1) != degrade(png, "noise", 2, seed=2)
    assert degrade(png, "blur", 2, seed=1) == degrade(png, "blur", 2, seed=2)


def test_the_seed_is_stable_per_image_kind_and_level():
    assert seed_for("V2-A01", "noise", 2) == seed_for("V2-A01", "noise", 2)
    assert len({seed_for("V2-A01", "noise", 2), seed_for("V2-A02", "noise", 2), seed_for("V2-A01", "noise", 3), seed_for("V2-A01", "jpeg", 2)}) == 4


def test_unknown_kind_or_level_is_refused_not_ignored():
    with pytest.raises(ValueError):
        degrade(base_png(), "sepia", 1)
    with pytest.raises(ValueError):
        degrade(base_png(), "blur", 4)


# --------------------------------------------------------------------------- 지시문 심기
@pytest.mark.parametrize("phrase_id", sorted(PHRASES))
@pytest.mark.parametrize("stealth", STEALTH)
def test_every_phrase_can_be_drawn_in_every_stealth(phrase_id, stealth):
    assert problems(_band_spec(640, PHRASES[phrase_id], stealth)) == []
    band = img(band_png(640, phrase_id, stealth))
    assert band.size == (640, BAND_H)
    assert ImageChops.invert(band).getbbox() is not None          # 띠에 무언가 그려졌다


@pytest.mark.parametrize("where", WHERE)
def test_the_original_pixels_are_untouched_by_an_injection(where):
    png = base_png()
    base, twin = img(png), img(inject_twin(png, "reviewer_polite", "plain", where))
    w, h = base.size
    assert twin.size == (w, h + BAND_H)
    region = (0, 0, w, h) if where == "bottom" else (0, BAND_H, w, h + BAND_H)
    assert ImageChops.difference(base, twin.crop(region)).getbbox() is None


def test_faint_and_small_bands_are_harder_to_see_than_plain_ones():
    def ink(stealth: str) -> int:
        hist = img(band_png(640, "authority", stealth)).convert("L").histogram()
        return sum((255 - v) * c for v, c in enumerate(hist))

    assert ink("plain") > ink("small") and ink("plain") > ink("faint")


def test_twin_texts_put_the_phrase_where_it_is_drawn():
    base = ["가상 적립식 투자 서비스", "시작하기"]
    assert twin_texts(base, "authority", "top")[0] == PHRASES["authority"]
    assert twin_texts(base, "authority", "bottom")[-1] == PHRASES["authority"]
    assert twin_texts(base, "authority", "bottom")[:-1] == base
    assert twin_id("V2-A01", "authority", "faint", "top") == "V2-A01~inj.authority.faint.top"


def test_unknown_phrase_stealth_or_place_is_refused():
    with pytest.raises(ValueError):
        band_png(640, "nope", "plain")
    with pytest.raises(ValueError):
        band_png(640, "authority", "invisible")
    with pytest.raises(ValueError):
        inject_twin(base_png(), "authority", "plain", "left")


# --------------------------------------------------------------------------- 심은 문장이 정답을 바꾸지 않는다
def benign(image_text: str | None) -> Creative:
    return Creative.model_validate({
        "ad_id": "t", "advertiser": {"name": "가상회사", "category": "general", "verified": True},
        "headline": "가상 문구 서비스", "body": "매달 원하는 만큼만 이용하세요", "cta": "시작하기", "image_text": image_text,
    })


@pytest.mark.parametrize("phrase_id", sorted(PHRASES))
def test_an_injected_phrase_alone_is_not_a_policy_violation(phrase_id):
    """쌍둥이의 정답은 원본과 같아야 한다 — 심은 문장 자체가 위반 어휘를 담으면 그 전제가 깨진다."""
    clean = review(benign("가상 적립식 투자 서비스\n시작하기"), ReviewConfig())
    with_phrase = review(benign(f"가상 적립식 투자 서비스\n시작하기\n{PHRASES[phrase_id]}"), ReviewConfig())
    assert clean.outcome == "APPROVE"
    assert with_phrase.outcome == "APPROVE", [(f.rule_id, f.quote) for f in with_phrase.findings]
