"""이미지 읽기 측정 묶음(docs/EVAL_VISION.md). 사례 하나 = 이미지 한 장, 모델은 이미지 속 글자와 시각 단서를 구조화해 돌려준다.

* ``vision`` — 변형 sonnet · haiku · opus. 읽은 결과를 사양의 정답과 견주고(읽기), 읽은 글자를 심사 소재의 ``image_text`` 로 넣었을 때 판정이 얼마나 달라지는지 잰다(판정 효과).
* 오프라인 팔(``offline``)은 모델 없이 *이미지 글자를 안 줌*(no_image)과 *사양의 정답 글자를 줌*(oracle)을 잰다 — 읽기가 완벽하다고 가정한 상한과 하한이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..llm import CaseOutcome, Image, LLMClient, LLMTask, ScriptedClient
from ..llm.core import Part
from ..suites import Driver, Suite, Variant, register
from .dataset import VisionExample, load_vision
from .evaluate import COMPOSITION_TAGS, decision_effect, decision_errors, score_reading, summarize_reading
from .read import ImageReading, read_image
from .spec import flags, texts

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data" / "vision" / "vision.jsonl"
IMAGES = "data/vision/images"      # 저장소 루트 기준 상대 경로 — 열쇠는 파일 내용으로 만든다
VARIANTS = {
    "sonnet": Variant("sonnet", "claude-sonnet-5-5", "헤드라인"),
    "haiku": Variant("haiku", "claude-haiku-5-5", "더 작은 모델"),
    "opus": Variant("opus", "claude-opus-5-5", "더 강한 모델"),
}
HARD_TAGS = ("tiny_print", "rotated", "low_contrast")


def load_all(path: Path | None = None) -> list[VisionExample]:
    return load_vision(path or DATA)


def image_part(item: VisionExample) -> Image:
    return Image(f"{IMAGES}/{item.id}.png")


def _compact(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in summary.items() if not k.endswith("_examples")}


def _score(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
    items = {it.id: it for it in load_all()}
    readings: dict[str, ImageReading | None] = {o.case: (ImageReading.model_validate(o.result) if o.result is not None else None) for o in outcomes}
    sel = [items[o.case] for o in outcomes]
    scores = {it.id: score_reading(it, readings[it.id]) for it in sel}
    strata: dict[str, Any] = {}
    for tag in (*COMPOSITION_TAGS, *HARD_TAGS):
        ids = [it.id for it in sel if tag in it.gold.tags]
        if ids:
            strata[tag] = _compact(summarize_reading([scores[i] for i in ids]))
    return {
        "cases": len(outcomes), "failed": [o.case for o in outcomes if o.result is None],
        "reading": summarize_reading(list(scores.values())), "reading_by_tag": strata, "decision": decision_effect(sel, readings),
    }


def _analyze(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
    """틀린 소재의 목록. 읽은 글을 줬을 때(read)와 정답 글자를 줬을 때(oracle)의 판정을 *소재 단위*로 견준다 — 집계 정확도가 같아도 틀린 소재가 같다는 뜻은 아니다."""
    items = {it.id: it for it in load_all()}
    readings: dict[str, ImageReading | None] = {o.case: (ImageReading.model_validate(o.result) if o.result is not None else None) for o in outcomes}
    sel = [items[o.case] for o in outcomes]
    errs = decision_errors(sel, readings)
    read_ids, oracle_ids = {e["id"] for e in errs["read"]}, {e["id"] for e in errs["oracle"]}
    scores = [score_reading(it, readings[it.id]) for it in sel]
    flag_fp: dict[str, list[str]] = {}
    flag_fn: dict[str, list[str]] = {}
    for sc in scores:
        for f in sc["flags_fp"]:
            flag_fp.setdefault(f, []).append(sc["id"])
        for f in sc["flags_fn"]:
            flag_fn.setdefault(f, []).append(sc["id"])
    return {
        "decision_errors": {"read": errs["read"], "oracle": errs["oracle"]},
        "read_worse_than_oracle": sorted(read_ids - oracle_ids), "read_better_than_oracle": sorted(oracle_ids - read_ids),
        "both_wrong": sorted(read_ids & oracle_ids),
        "reading_misses": [{"id": sc["id"], "missed": sc["missed"], "extra": sc["hallucinated"]} for sc in scores if sc["missed"] or sc["hallucinated"]],
        "flag_false_positives": flag_fp, "flag_false_negatives": flag_fn,
    }


def _standin(payload: dict[str, Any]) -> ScriptedClient:
    item: VisionExample = payload["item"]

    def handler(task: LLMTask, parts: list[Part]) -> dict[str, Any]:
        return {"texts": [{"text": t, "legible": True} for t in texts(item.image)], "flags": flags(item.image), "description": "광고 이미지"}

    return ScriptedClient(handler)


def offline() -> dict[str, Any]:
    """모델 없는 두 팔: 이미지 글자를 안 줌 / 사양의 정답 글자를 줌."""
    eff = decision_effect(load_all(), {})
    eff.pop("read", None)
    eff["image_only"].pop("read", None)
    for block in eff["by_tag"].values():
        block.pop("read", None)
    return {"items": len(load_all()), "decision": eff}


@register("vision")
def vision() -> Suite:
    items = load_all()

    def cases(variant: str) -> list[tuple[str, Any]]:
        return [(it.id, {"item": it}) for it in items]

    def driver(variant: str) -> Driver:
        def run(payload: dict[str, Any], client: LLMClient) -> dict[str, Any]:
            return read_image(client, image_part(payload["item"])).model_dump()

        return run

    return Suite(
        "vision", "이미지 읽기(VLM): 글자·시각 단서와 심사 판정에 주는 효과", "docs/EVAL_VISION.md", VARIANTS, cases, driver, _standin, _score,
        offline=offline, check_case=lambda: (items[0].id, {"item": items[0]}), analyze=_analyze,
    )
