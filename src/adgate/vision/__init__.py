"""합성 광고 이미지: 사양(DSL) → PNG 렌더링, 이미지 읽기(VLM) 과제와 평가."""

from .render import RenderError, problems, render, render_png, unrenderable
from .spec import FLAGS, ImageSpec, Item, flags, hard_texts, image_text, texts

__all__ = ["FLAGS", "ImageSpec", "Item", "RenderError", "flags", "hard_texts", "image_text", "problems", "render", "render_png", "texts", "unrenderable"]
