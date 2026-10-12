"""이미지 평가셋: 소재(이미지 속 글자 없이) + 이미지 사양 + 정답. 이미지는 사양에서 그려 PNG 로 *커밋*한다(열쇠가 파일 내용이라 재생이 환경에 흔들리지 않는다)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..eval.dataset import Example, Gold
from ..models import Creative
from .render import render_png
from .spec import ImageSpec, image_text


class VisionExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    creative: Creative
    image: ImageSpec
    gold: Gold

    def example(self, text: str | None) -> Example:
        """심사 평가용 ``Example``. ``text`` 가 이미지 속 글자로 소재에 들어간다(None 이면 이미지를 못 읽은 것)."""
        return Example(id=self.id, creative=self.creative.model_copy(update={"image_text": text}), gold=self.gold)

    def oracle(self) -> Example:
        """이미지 속 글자를 사양에서 그대로 옮긴 *정답* 입력(읽기가 완벽하다고 가정한 상한)."""
        return self.example(image_text(self.image))


def load_vision(path: str | Path) -> list[VisionExample]:
    out: list[VisionExample] = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(VisionExample.model_validate(json.loads(line)))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"{path}:{n}: {str(exc)[:300]}") from exc
    return out


def image_file(root: str | Path, item_id: str) -> Path:
    return Path(root) / "images" / f"{item_id}.png"


def build_images(items: list[VisionExample], root: str | Path) -> list[Path]:
    """사양을 PNG 로 그려 ``<root>/images/<id>.png`` 에 쓴다(개발 시점 한 번. 결과를 커밋한다)."""
    return [render_png(it.image, image_file(root, it.id)) for it in items]
