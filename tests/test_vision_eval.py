"""이미지 읽기 평가(vision/evaluate.py, vision/suite.py)의 시험. 채점기가 틀리면 측정이 의미를 잃는다.

이 시험은 평가셋의 *수치*를 단정하지 않는다(사전등록 전에 첫 접촉을 보지 않으려고). 구조·불변식·악용/정상 유사 쌍만 검사한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image as PILImage  # noqa: E402

from adgate.eval import dataset  # noqa: E402
from adgate.llm import CaseOutcome  # noqa: E402
from adgate.suites import get  # noqa: E402
from adgate.vision.evaluate import COMPOSITION_TAGS, cer, found_in, score_reading, similar, summarize_reading  # noqa: E402
from adgate.vision.read import ImageReading, ReadText  # noqa: E402
from adgate.vision.spec import flags, texts  # noqa: E402
from adgate.vision.suite import (  # noqa: E402
    DATA,
    HARD_TAGS,
    ROOT,
    _analyze,
    _score,
    _standin,
    image_part,
    load_all,
    offline,
    vision,
)

ITEMS = load_all()


def oracle_reading(item) -> ImageReading:  # type: ignore[no-untyped-def]
    return ImageReading(texts=[ReadText(text=t, legible=True) for t in texts(item.image)], flags=flags(item.image), description="x")


# --- 데이터 ------------------------------------------------------------------------------------------------------------------------


def test_dataset_shape_and_images() -> None:
    assert len(ITEMS) == 60 and len({it.id for it in ITEMS}) == 60
    for it in ITEMS:
        path = ROOT / image_part(it).path
        assert path.exists(), path
        with PILImage.open(path) as im:
            assert im.size == (it.image.canvas.w, it.image.canvas.h)
        assert sum(t in it.gold.tags for t in COMPOSITION_TAGS) == 1, it.id        # 구성 태그는 정확히 하나
    assert {it.id[2] for it in ITEMS} == set("ABCDEF")


def test_dataset_is_frozen_in_the_manifest() -> None:
    status = dataset.verify_manifest(ROOT / "data" / "MANIFEST.sha256")
    assert status["vision/vision.jsonl"] is True
    assert all(status[f"vision/images/{it.id}.png"] for it in ITEMS)
    assert DATA.name == "vision.jsonl"


# --- 읽기 채점 ----------------------------------------------------------------------------------------------------------------------


def test_found_in_is_granularity_tolerant_but_not_a_free_pass() -> None:
    hay = "월 30만원씩 자동으로 나눠 담는 적립식 투자 원금 보장형 플랜 3분 성향 진단 후 바로 시작"
    assert found_in("원금 보장형 플랜", hay)                      # 덩어리를 합쳐 읽어도 찾는다
    assert found_in("원금 보장형 플랜", "원금 보장형 플렌")         # 한 글자 오독은 허용(유사도 0.85)
    assert not found_in("원금 손실이 발생할 수 있으며", hay)         # 없는 글은 못 찾는다
    assert not found_in("", hay) and not found_in("가", "")
    assert not found_in("투자 원금의 일부 또는 전부 손실이 발생할 수 있으며", "투자 원금")   # 앞부분만 있는 것은 찾은 게 아니다


def test_similar_and_cer_basics() -> None:
    assert similar("국내 1위", "국내1위") and not similar("국내 1위", "국내 2위 건강")
    assert cer("abcd", "abcd") == 0.0 and cer("abcd", "") == 1.0 and 0 < cer("abcd", "abxd") < 1


def test_oracle_reading_scores_perfectly() -> None:
    for it in ITEMS[:12]:
        s = score_reading(it, oracle_reading(it))
        assert s["all_hit"] and s["hit"] == s["gold_n"] == s["hit_concat"] and not s["hallucinated"], it.id
        assert not s["flags_fp"] and not s["flags_fn"], it.id


def test_merged_lines_lower_strict_recall_but_not_concat_recall() -> None:
    it = next(x for x in ITEMS if len(texts(x.image)) >= 4)
    gold = texts(it.image)
    merged = ImageReading(texts=[ReadText(text=" ".join(gold), legible=True)], flags=flags(it.image), description="x")
    s = score_reading(it, merged)
    assert s["hit_concat"] == s["gold_n"]
    assert s["hit"] < s["gold_n"] and s["hallucinated"]            # 덩어리 단위 채점은 합쳐 읽은 것을 틀린 것으로 센다 — 그래서 둘 다 보고한다


def test_hallucinated_and_missed_texts_are_counted() -> None:
    it = next(x for x in ITEMS if len(texts(x.image)) >= 3)
    gold = texts(it.image)
    r = ImageReading(texts=[*[ReadText(text=t, legible=True) for t in gold[:-1]], ReadText(text="지금 바로 100% 환불 보장", legible=True)], flags=[], description="x")
    s = score_reading(it, r)
    assert s["missed"] == [gold[-1]] and s["hallucinated"] == ["지금 바로 100% 환불 보장"] and not s["all_hit"]


def test_unreadable_image_scores_as_a_failed_read() -> None:
    s = score_reading(ITEMS[0], None)
    assert not s["read"] and s["cer"] == 1.0 and s["hit"] == 0
    summ = summarize_reading([s])
    assert summ["read_failed"] == 1


# --- 묶음 -------------------------------------------------------------------------------------------------------------------------


def test_registry_and_variants() -> None:
    s = get("vision")
    assert set(s.variants) == {"sonnet", "haiku", "opus"} and len(s.cases("sonnet")) == 60 and s.offline is not None


def test_standin_reading_equals_oracle_and_decisions_match(tmp_path: Path) -> None:
    """대역이 정답 글자를 읽어 주면 '읽은 글자를 준' 팔은 '정답 글자를 준' 팔과 같아야 한다(불변식)."""
    s = vision()
    driver = s.driver("sonnet")
    outcomes = []
    for cid, payload in s.cases("sonnet")[:20]:
        out = driver(payload, _standin(payload))
        outcomes.append(CaseOutcome(case=cid, result=out))
    res = _score("sonnet", outcomes)
    assert res["failed"] == [] and res["reading"]["items"] == 20
    assert res["reading"]["text_recall"]["value"] == 1.0 and res["reading"]["hallucinated_items"] == 0
    assert res["decision"]["read"]["accuracy"] == res["decision"]["oracle"]["accuracy"]
    assert set(res["reading_by_tag"]) <= {*COMPOSITION_TAGS, *HARD_TAGS}


def test_a_failed_read_is_never_approved() -> None:
    s = vision()
    outcomes = [CaseOutcome(case=cid, result=None, error="LLMTruncated") for cid, _ in s.cases("sonnet")[:6]]
    res = _score("sonnet", outcomes)
    assert res["failed"] == [cid for cid, _ in s.cases("sonnet")[:6]]
    read = res["decision"]["read"]
    assert read["pred_counts"] == {"APPROVE": 0, "REVIEW": 6, "REJECT": 0}         # 읽지 못한 소재는 승인하지 않고 사람에게 보낸다(fail-closed)


def test_offline_has_no_model_arm() -> None:
    out = offline()
    assert out["items"] == 60 and set(out["decision"]) >= {"no_image", "oracle", "image_only", "by_tag"}
    assert "read" not in out["decision"] and "read" not in out["decision"]["image_only"]
    assert all("read" not in b for b in out["decision"]["by_tag"].values())


# --- 실패 분석 ---------------------------------------------------------------------------------------------------------------------


def _outcomes(n: int, mutate=None):  # type: ignore[no-untyped-def]
    s = vision()
    driver = s.driver("sonnet")
    out = []
    for cid, payload in s.cases("sonnet")[:n]:
        res = driver(payload, _standin(payload))
        if mutate is not None:
            res = mutate(cid, payload["item"], res)
        out.append(CaseOutcome(case=cid, result=res))
    return out


def test_analyze_with_perfect_reading_has_no_read_only_errors() -> None:
    """완벽히 읽으면 read 팔의 틀린 소재는 oracle 팔과 같다 — 엔진의 몫만 남는다."""
    a = _analyze("sonnet", _outcomes(20))
    assert a["read_worse_than_oracle"] == [] and a["read_better_than_oracle"] == []
    assert {e["id"] for e in a["decision_errors"]["read"]} == {e["id"] for e in a["decision_errors"]["oracle"]} == set(a["both_wrong"])
    assert a["reading_misses"] == [] and a["flag_false_positives"] == {} and a["flag_false_negatives"] == {}


def test_analyze_finds_items_where_a_missed_disclosure_flips_the_decision() -> None:
    """작은 글씨 고지를 읽지 못하면 FIN-01 오탐으로 read 팔만 틀린다(V-A05·A06: 정답 APPROVE → REJECT). 같은 누락이 이미 REJECT 인 소재(V-A01)의 판정은 바꾸지 못한다."""
    from adgate.vision.spec import hard_texts

    drop = {"V-A01", "V-A05", "V-A06"}

    def drop_small_print(cid, item, res):  # type: ignore[no-untyped-def]
        if cid in drop:
            hard = set(hard_texts(item.image))
            res = {**res, "texts": [t for t in res["texts"] if t["text"] not in hard]}
        return res

    a = _analyze("sonnet", _outcomes(60, drop_small_print))
    assert {m["id"] for m in a["reading_misses"]} >= drop
    assert {"V-A05", "V-A06"} <= set(a["read_worse_than_oracle"]) and "V-A01" not in a["read_worse_than_oracle"]
    assert all(e["pred_findings"][0][0] == "AD-FIN-01" for e in a["decision_errors"]["read"] if e["id"] in ("V-A05", "V-A06"))


def test_analyze_counts_a_failed_read_as_an_error_unless_gold_is_review() -> None:
    s = vision()
    outcomes = [CaseOutcome(case=cid, result=None, error="LLMTruncated") for cid, _ in s.cases("sonnet")[:10]]
    a = _analyze("sonnet", outcomes)
    wrong = {e["id"] for e in a["decision_errors"]["read"]}
    gold = {it.id: it.gold.decision for it in ITEMS}
    assert wrong == {cid for cid, _ in s.cases("sonnet")[:10] if gold[cid] != "REVIEW"}     # 읽지 못하면 REVIEW 로 보낸다 → 정답이 REVIEW 가 아닌 소재만 틀린 것으로 센다
