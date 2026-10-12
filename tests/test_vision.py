"""이미지 사양(DSL)과 렌더러. 정답(이미지 속 글자·시각 단서)이 사양에서 기계적으로 나오는지, 그릴 수 없는 사양이 조용히 넘어가지 않는지 검사한다."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PIL")

from pydantic import ValidationError  # noqa: E402

from adgate.vision import (  # noqa: E402
    ImageSpec,
    RenderError,
    flags,
    hard_texts,
    image_text,
    problems,
    render,
    render_png,
    texts,
    unrenderable,
)
from adgate.vision.render import FONT_DIR, covered_codepoints, fit, font, wrap  # noqa: E402

EXAMPLE = {
    "canvas": {"bg": "#f6efe4", "bg2": "#e9dcc6"},
    "items": [
        {"kind": "text", "box": [6, 4, 88, 14], "text": "한 달 만에 10kg 감량!", "bold": True, "color": "#b3261e", "align": "center"},
        {"kind": "product", "box": [8, 20, 44, 58], "shape": "bottle", "color": "#3a8f6a", "label": "슬림핏"},
        {"kind": "badge", "box": [58, 22, 30, 22], "shape": "burst", "text": "국내 1위", "award": True},
        {"kind": "timer", "box": [8, 80, 50, 8], "text": "오늘만 00:59:59"},
        {"kind": "button", "box": [62, 82, 30, 9], "text": "지금 구매"},
        {"kind": "fine_print", "box": [6, 93, 88, 5], "text": "※ 개인에 따라 결과가 다를 수 있습니다.", "size": 11},
    ],
}


def spec(d: dict | None = None) -> ImageSpec:
    return ImageSpec.model_validate(d or EXAMPLE)


# --------------------------------------------------------------------------- 정답은 사양에서 나온다
def test_image_text_and_flags_are_derived_from_the_spec_in_reading_order():
    s = spec()
    assert texts(s) == ["한 달 만에 10kg 감량!", "슬림핏", "국내 1위", "오늘만 00:59:59", "지금 구매", "※ 개인에 따라 결과가 다를 수 있습니다."]
    assert image_text(s) == "\n".join(texts(s))
    assert flags(s) == ["countdown_timer", "award_badge", "tiny_print"]
    assert hard_texts(s) == ["※ 개인에 따라 결과가 다를 수 있습니다."]


def test_each_visual_cue_kind_maps_to_its_flag():
    cases = {
        "before_after": {"kind": "split", "box": [10, 10, 80, 50], "left_label": "BEFORE", "right_label": "AFTER"},
        "fake_system_ui": {"kind": "popup", "box": [10, 10, 80, 40], "title": "경고", "text": "위험이 발견되었습니다", "buttons": ["확인"]},
        "rating_stars": {"kind": "stars", "box": [10, 10, 60, 8], "text": "4.9"},
        "low_contrast": {"kind": "text", "box": [10, 10, 60, 10], "text": "흐린 문구", "opacity": 0.3},
        "tiny_print": {"kind": "text", "box": [10, 10, 80, 6], "text": "작은 글씨", "size": 10},
    }
    for flag, item in cases.items():
        assert flag in flags(ImageSpec.model_validate({"items": [item]})), flag
    assert flags(ImageSpec.model_validate({"items": [{"kind": "text", "box": [10, 10, 60, 10], "text": "보통 글씨"}]})) == []


def test_popup_and_split_texts_include_every_visible_string():
    s = ImageSpec.model_validate({"items": [
        {"kind": "popup", "box": [5, 5, 90, 40], "title": "알림", "text": "본문", "buttons": ["예", "아니오"]},
        {"kind": "split", "box": [5, 50, 90, 40], "left_label": "전", "right_label": "후", "text": "캡션"},
    ]})
    assert texts(s) == ["알림", "본문", "예", "아니오", "전", "후", "캡션"]


# --------------------------------------------------------------------------- 사양 검증
@pytest.mark.parametrize(
    "item",
    [
        {"kind": "text", "box": [50, 10, 60, 10], "text": "칸이 캔버스 밖"},
        {"kind": "text", "box": [10, 10, 60, 10]},                                         # text 없음
        {"kind": "text", "box": [10, 10, 60, 10], "text": "색 오류", "color": "red"},
        {"kind": "product", "box": [10, 10, 30, 30], "shape": "spaceship"},
        {"kind": "split", "box": [10, 10, 60, 40], "left_label": "전"},                     # right_label 없음
        {"kind": "text", "box": [10, 10, 60, 10], "text": "x", "opacity": 0},
        {"kind": "text", "box": [10, 10, 60, 10], "text": "x", "mystery": 1},              # 알 수 없는 키
    ],
)
def test_invalid_items_are_rejected(item):
    with pytest.raises(ValidationError):
        ImageSpec.model_validate({"items": [item]})


# --------------------------------------------------------------------------- 렌더링
def test_render_is_deterministic_and_actually_draws(tmp_path):
    img = render(spec())
    assert img.size == (800, 800) and img.mode == "RGB"
    assert render(spec()).tobytes() == img.tobytes()                                       # 같은 사양 → 같은 그림
    assert len(img.resize((40, 40)).getcolors(maxcolors=1600) or []) > 20                                   # 단색이 아니다
    out = render_png(spec(), tmp_path / "a" / "x.png")
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_every_product_shape_and_badge_shape_renders():
    items = [{"kind": "product", "box": [5, 5, 40, 40], "shape": sh, "label": "라벨"} for sh in
             ("bottle", "box", "can", "bag", "phone", "shoe", "tube", "cup", "generic")]
    for it in items:
        assert problems(ImageSpec.model_validate({"items": [it]})) == [], it["shape"]
    for shape in ("circle", "burst", "ribbon"):
        assert problems(ImageSpec.model_validate({"items": [{"kind": "badge", "box": [10, 10, 30, 20], "shape": shape, "text": "배지"}]})) == []


def test_unrenderable_characters_fail_loudly_instead_of_drawing_boxes():
    assert unrenderable("수익 보장 ★ ₩ ※ → 가격") == []
    assert unrenderable("보장😀") == ["😀"] and unrenderable("保障") == ["保", "障"]
    bad = ImageSpec.model_validate({"items": [{"kind": "text", "box": [10, 10, 80, 20], "text": "무조건 保障"}]})
    assert any("글꼴에 없는 글자" in p for p in problems(bad))
    with pytest.raises(RenderError):
        render(bad)


def test_text_that_cannot_fit_is_an_error_not_a_clipped_image():
    long = "이 문구는 아주 작은 칸에는 도저히 들어갈 수 없는 매우 긴 문장입니다. " * 6
    tiny_box = ImageSpec.model_validate({"items": [{"kind": "text", "box": [10, 10, 12, 4], "text": long}]})
    assert problems(tiny_box) and "들어가지 않습니다" in problems(tiny_box)[0]
    fixed = ImageSpec.model_validate({"items": [{"kind": "text", "box": [10, 10, 20, 3], "text": "고정 크기 글자는 칸이 모자라면 오류", "size": 40}]})
    assert problems(fixed)


def test_auto_fit_prefers_unbroken_words_and_wrap_handles_long_tokens():
    size, lines = fit("슬림핏 다이어트", 100, 150, True)
    assert lines == ["슬림핏", "다이어트"] and size >= 20                                    # "다이어/트" 로 쪼개지 않는다
    assert fit("프로", 90, 140, True)[1] == ["프로"]                                          # 세로로 쌓이지 않는다
    f = font(20)
    assert all(f.getlength(line) <= 120 for line in wrap("가" * 40, f, 120))                   # 낱말이 너무 길면 글자 단위로 쪼갠다
    with pytest.raises(RenderError):
        fit("가" * 400, 60, 40, False)


def test_declared_font_coverage_matches_the_bundled_fonts():
    ft = pytest.importorskip("fontTools.ttLib")
    declared = covered_codepoints()
    for style in ("Regular", "Bold"):
        cmap = set(ft.TTFont(FONT_DIR / f"GothicA1-{style}.subset.ttf").getBestCmap())
        assert {c for c in cmap if c >= 0x20} == set(declared), style                          # 그린다고 한 글자와 글꼴이 가진 글자가 같다
    assert 0xAC00 in declared and 0x1F600 not in declared and ord("★") in declared


def test_font_license_and_provenance_files_ship_with_the_fonts():
    assert "SIL OPEN FONT LICENSE" in (FONT_DIR / "OFL.txt").read_text(encoding="utf-8")
    readme = (FONT_DIR / "README.md").read_text(encoding="utf-8")
    assert "211151bea98098c579610ab1114bb1bfe909057207db561f357b896d076c21ac" in readme and "OFL" in readme
    json.loads((FONT_DIR / "coverage.json").read_text(encoding="utf-8"))
