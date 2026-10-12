"""후보 → 광고 이미지 사양. 상품 이미지의 상품 일러스트를 그대로 쓰고, 문구·배지·버튼을 얹는다(결정론)."""

from __future__ import annotations

from ..vision import ImageSpec, Item, image_text
from ..vision.spec import Canvas
from .models import Candidate, Constraints


def _luma(hex_color: str) -> float:
    c = hex_color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return 0.299 * r + 0.587 * g + 0.114 * b


def image_spec_for(c: Candidate, product_image: ImageSpec, cons: Constraints | None = None) -> ImageSpec:
    """hero: 위에 한 줄 문구, 가운데 상품, 아래 버튼. badge: 거기에 배지 하나."""
    canvas = Canvas(w=800, h=800, bg=product_image.canvas.bg, bg2=product_image.canvas.bg2)
    dark = _luma(product_image.canvas.bg) < 110
    ink = "#ffffff" if dark else "#1d2430"
    product = next((it for it in product_image.items if it.kind == "product"), None)
    items: list[Item] = [Item(kind="text", box=(6, 4, 88, 20), text=c.image.overlay_text, bold=True, align="center", color=ink)]
    if product is not None:
        items.append(product.model_copy(update={"box": (22, 26, 56, 52)}))
    items.append(Item(kind="button", box=(28, 83, 44, 10), text=c.cta))
    if c.image.layout == "badge" and c.image.badge_text:
        items.append(Item(kind="badge", box=(66, 24, 26, 22), shape="burst", text=c.image.badge_text))
    return ImageSpec(canvas=canvas, items=items)


def overlay_image_text(c: Candidate, product_image: ImageSpec) -> str:
    """이 후보를 그렸을 때 이미지 속에 있게 될 글자(심사 소재의 ``image_text``)."""
    return image_text(image_spec_for(c, product_image))
