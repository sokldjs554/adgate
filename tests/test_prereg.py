"""사전등록 해시 등록부, 그리고 회색지대 셋(gray)의 동결·분쟁 규칙."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adgate import prereg
from adgate.eval import agreement, dataset

ROOT = Path(__file__).resolve().parent.parent
DOC = "# 제목\n\n## 1. 질문\n본문 하나\n\n## 2. 예상\n예상 하나\n\n## 3. 결과\n나중에 쓴다\n"


def test_section_body_is_bounded_by_headings():
    assert prereg.section_body(DOC, "## 1.", "## 3.") == "## 1. 질문\n본문 하나\n\n## 2. 예상\n예상 하나"
    assert prereg.section_body(DOC, "## 3.", None).endswith("나중에 쓴다")
    with pytest.raises(ValueError, match="찾지 못했다"):
        prereg.section_body(DOC, "## 9.", None)


def test_registered_section_cannot_change_but_the_results_after_it_can(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/X.md").write_text(DOC, encoding="utf-8")
    prereg.register("docs/X.md", "## 1.", "## 3.", root=tmp_path)
    assert prereg.check(tmp_path) == []
    (tmp_path / "docs/X.md").write_text(DOC.replace("나중에 쓴다", "결과: 예상이 빗나갔다\n\n## 4. 덧붙임\n더"), encoding="utf-8")
    assert prereg.check(tmp_path) == [], "끝 제목 뒤에 결과를 덧붙이는 것은 허용"
    (tmp_path / "docs/X.md").write_text(DOC.replace("예상 하나", "예상을 고쳐 썼다"), encoding="utf-8")
    assert len(prereg.check(tmp_path)) == 1, "사전등록 본문을 고치면 걸린다"


def test_registering_the_same_section_twice_is_refused(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/X.md").write_text(DOC, encoding="utf-8")
    prereg.register("docs/X.md", "## 1.", "## 3.", root=tmp_path)
    with pytest.raises(ValueError, match="이미 등록"):
        prereg.register("docs/X.md", "## 1.", "## 3.", root=tmp_path)


def test_repository_preregistrations_are_unchanged():
    assert prereg.check(ROOT) == []
    assert any(e["doc"] == "docs/EVAL_GRAY.md" for e in prereg.load(ROOT))


# --------------------------------------------------------------------------- 회색지대 셋
def test_fleiss_kappa_known_values():
    assert agreement.fleiss_kappa([["A", "A", "A"], ["B", "B", "B"]]) == pytest.approx(1.0)
    # Fleiss 의 고전 예: 평가자 3명이 한 대상은 전원 일치, 다른 대상은 2:1
    k = agreement.fleiss_kappa([["A", "A", "A"], ["A", "A", "B"], ["B", "B", "B"], ["A", "B", "B"]])
    assert -1 <= k <= 1 and k == pytest.approx(0.3333, abs=0.01)


def _ex(i: str, decision: str, tags: list[str]) -> dataset.Example:
    return dataset.Example.model_validate({
        "id": i, "gold": {"decision": decision, "violations": [], "tags": tags},
        "creative": {"ad_id": f"ad-{i}", "advertiser": {"name": "가", "category": "general", "verified": True}, "headline": "h", "body": "b", "cta": "c"},
    })


def test_disputed_rule_marks_two_of_three():
    exs = [_ex("a", "APPROVE", ["gray"]), _ex("b", "APPROVE", ["gray"])]
    row = lambda i, d: {"id": i, "decision": d, "violations": []}  # noqa: E731
    labs = {
        "R1": {"a": row("a", "REVIEW"), "b": row("b", "APPROVE")},
        "R2": {"a": row("a", "REVIEW"), "b": row("b", "REVIEW")},
        "R3": {"a": row("a", "APPROVE"), "b": row("b", "APPROVE")},
    }
    res = agreement.compare_multi(exs, labs, alt={"a": "REVIEW"})
    assert res["disputed"]["ids"] == ["a"]
    assert res["lenient_agreement_with_author_or_alt"]["k"] == 5   # 6표 중 b 의 R2 REVIEW 한 표만 정답·대안이 아니다


def test_gray_dataset_is_frozen_and_its_disputed_tags_follow_the_rule():
    data = ROOT / "data"
    assert dataset.verify_manifest(data / "MANIFEST.sha256")["gray.jsonl"] is True
    exs = dataset.load(data / "gray.jsonl")
    assert len(exs) == 120 and dataset.validate(exs) == []
    labs = agreement.load_multi(data / "labeler_gray.jsonl")
    assert sorted(labs) == ["R1", "R2", "R3"] and all(len(v) == 120 for v in labs.values())
    res = agreement.compare_multi(exs, labs)
    tagged = sorted(ex.id for ex in exs if "disputed" in ex.gold.tags)
    assert tagged == sorted(res["disputed"]["ids"]), "disputed 태그는 라벨러 판정에서 기계적으로 나와야 한다"
    kinds = {t: sum(t in ex.gold.tags for ex in exs) for t in ("clear", "normal", "gray")}
    assert kinds == {"clear": 30, "normal": 30, "gray": 60}


def test_gray_author_notes_name_every_item_and_alt_decisions_are_valid():
    rows = [json.loads(ln) for ln in (ROOT / "data/gray_notes.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    ids = {ex.id for ex in dataset.load(ROOT / "data/gray.jsonl")}
    assert {r["id"] for r in rows} == ids
    assert all(r.get("alt_decision") in (None, "APPROVE", "REVIEW", "REJECT") for r in rows)
