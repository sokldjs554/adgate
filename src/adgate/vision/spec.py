"""합성 광고 이미지의 사양(DSL). 평가셋 저자가 JSON 으로 쓰고 ``render`` 가 PNG 로 그린다.

이미지 *내용*을 사양으로 정의하는 이유: 정답(이미지 속 글자, 시각 단서)을 사람이 따로 옮겨 적지 않아도 사양에서 **기계적으로** 나온다.
저자가 정답을 손으로 옮기다 생기는 불일치가 없고, 렌더러가 그리지 못하는 글자는 그리기 전에 오류로 알린다.

좌표는 캔버스에 대한 백분율 ``box = [x, y, w, h]`` 다(0–100). 항목은 목록 순서대로(=읽는 순서대로) 그려진다.

| kind | 뜻 | 필요한 필드 |
|---|---|---|
| ``product`` | 상품 일러스트(병·상자·캔 …) | ``shape``, 선택 ``label``(상품에 인쇄된 글자), ``color`` |
| ``text`` | 글자 블록(제목·문구) | ``text``, 선택 ``size``(생략하면 박스에 맞춤)·``bold``·``align``·``color``·``bg``·``rotate``·``opacity`` |
| ``badge`` | 원·별·리본 배지 | ``text``, 선택 ``shape``(circle/burst/ribbon)·``award``(수상·순위 배지인가) |
| ``button`` | 행동 유도 버튼 | ``text`` |
| ``timer`` | 카운트다운 배너 | ``text`` |
| ``split`` | 전·후 비교 화면 | ``left_label``, ``right_label``, 선택 ``text``(캡션) |
| ``popup`` | 시스템 경고창처럼 보이는 창 | ``title``, ``text``, 선택 ``buttons`` |
| ``fine_print`` | 작은 글씨 고지 | ``text``, 선택 ``size``(기본 11) |
| ``stars`` | 별점 | 선택 ``text``(예: "4.9 (리뷰 3,210)") |
| ``logo`` | 상표 모양 표지 | ``text`` |
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["product", "text", "badge", "button", "timer", "split", "popup", "fine_print", "stars", "logo"]
PRODUCT_SHAPES = ("bottle", "box", "can", "bag", "phone", "shoe", "tube", "cup", "generic")
BADGE_SHAPES = ("circle", "burst", "ribbon")
FLAGS = ("before_after", "countdown_timer", "fake_system_ui", "award_badge", "tiny_print", "low_contrast", "rating_stars")
TINY_PX = 12          # 이 크기 이하로 명시한 글자는 '작은 글씨'
FAINT_OPACITY = 0.4   # 이 투명도 이하의 글자는 '흐린 글씨'
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_NEEDS_TEXT = ("text", "badge", "button", "timer", "fine_print", "logo")


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    box: tuple[float, float, float, float]
    text: str | None = Field(default=None, max_length=300)
    color: str | None = None
    bg: str | None = None
    size: int | Literal["auto"] = "auto"
    bold: bool = False
    align: Literal["left", "center", "right"] = "left"
    rotate: float = Field(default=0.0, ge=-45.0, le=45.0)
    opacity: float = Field(default=1.0, gt=0.0, le=1.0)
    shape: str | None = None
    label: str | None = Field(default=None, max_length=60)
    award: bool = False
    left_label: str | None = Field(default=None, max_length=40)
    right_label: str | None = Field(default=None, max_length=40)
    left_color: str | None = None
    right_color: str | None = None
    title: str | None = Field(default=None, max_length=60)
    buttons: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def _check(self) -> Item:
        x, y, w, h = self.box
        if not (0 <= x <= 100 and 0 <= y <= 100 and w > 0 and h > 0 and x + w <= 100.01 and y + h <= 100.01):
            raise ValueError(f"box {self.box} 가 캔버스(0–100) 밖입니다")
        for name in ("color", "bg", "left_color", "right_color"):
            v = getattr(self, name)
            if v is not None and not _COLOR.match(v):
                raise ValueError(f"{name} 는 #rrggbb 여야 합니다: {v!r}")
        if self.kind in _NEEDS_TEXT and not self.text:
            raise ValueError(f"{self.kind} 는 text 가 필요합니다")
        if self.kind == "product" and self.shape not in PRODUCT_SHAPES:
            raise ValueError(f"product.shape 는 {PRODUCT_SHAPES} 중 하나")
        if self.kind == "badge" and self.shape not in (None, *BADGE_SHAPES):
            raise ValueError(f"badge.shape 는 {BADGE_SHAPES} 중 하나")
        if self.kind == "split" and not (self.left_label and self.right_label):
            raise ValueError("split 은 left_label 과 right_label 이 필요합니다")
        if self.kind == "popup" and not (self.title and self.text):
            raise ValueError("popup 은 title 과 text 가 필요합니다")
        if self.size != "auto" and not 6 <= int(self.size) <= 200:
            raise ValueError("size 는 6–200")
        return self


class Canvas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    w: int = Field(default=800, ge=200, le=1600)
    h: int = Field(default=800, ge=200, le=1600)
    bg: str = "#f4f1ea"
    bg2: str | None = None  # 있으면 위(bg)에서 아래(bg2)로 그라데이션

    @model_validator(mode="after")
    def _check(self) -> Canvas:
        for v in (self.bg, self.bg2):
            if v is not None and not _COLOR.match(v):
                raise ValueError(f"색은 #rrggbb 여야 합니다: {v!r}")
        return self


class ImageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canvas: Canvas = Field(default_factory=Canvas)
    items: list[Item] = Field(min_length=1, max_length=16)


def texts(spec: ImageSpec) -> list[str]:
    """이미지에 *그려지는* 모든 글자 덩어리(읽는 순서). 정답 ``image_text`` 의 재료다."""
    out: list[str] = []
    for it in spec.items:
        if it.kind == "popup":
            out += [t for t in (it.title, it.text, *it.buttons) if t]
        elif it.kind == "split":
            out += [t for t in (it.left_label, it.right_label, it.text) if t]
        elif it.kind == "product":
            if it.label:
                out.append(it.label)
        elif it.text:
            out.append(it.text)
    return out


def image_text(spec: ImageSpec) -> str:
    """심사 소재의 ``image_text`` 로 쓰는 문자열: 글자 덩어리를 줄바꿈으로 이은 것."""
    return "\n".join(texts(spec))


def flags(spec: ImageSpec) -> list[str]:
    """사양에서 기계적으로 나오는 시각 단서."""
    found: set[str] = set()
    for it in spec.items:
        if it.kind == "split":
            found.add("before_after")
        elif it.kind == "timer":
            found.add("countdown_timer")
        elif it.kind == "popup":
            found.add("fake_system_ui")
        elif it.kind == "stars":
            found.add("rating_stars")
        elif it.kind == "badge" and it.award:
            found.add("award_badge")
        carries_text = it.kind in ("text", "fine_print", "button", "badge", "timer", "logo", "stars")
        if it.kind == "fine_print" or (carries_text and it.size != "auto" and int(it.size) <= TINY_PX):
            found.add("tiny_print")
        if carries_text and it.opacity <= FAINT_OPACITY:
            found.add("low_contrast")
    return [f for f in FLAGS if f in found]


def hard_texts(spec: ImageSpec) -> list[str]:
    """사람 눈에도 읽기 어려운 글자(작은 글씨·흐린 글씨). 읽기 평가에서 '어려운 것'으로 따로 센다."""
    out: list[str] = []
    for it in spec.items:
        if it.kind in ("text", "fine_print", "button", "badge", "timer", "logo", "stars") and it.text:
            tiny = it.kind == "fine_print" or (it.size != "auto" and int(it.size) <= TINY_PX)
            if tiny or it.opacity <= FAINT_OPACITY:
                out.append(it.text)
    return out
