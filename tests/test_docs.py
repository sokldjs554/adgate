from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from adgate import docs
from adgate.cli import main

SOURCES = {"results": {"test": {"full": {"accuracy": {"k": 76, "n": 77, "value": 0.987, "ci": [0.93, 1.0]}, "macro_f1": 0.98}}}}


def test_fill_markers_replaces_only_the_inside_of_markers():
    text = "정확도 <!-- num:results.test.full.accuracy|rpct1 -->0<!-- /num --> 입니다. 손으로 쓴 98.7% 는 그대로."
    out = docs.fill_markers(text, SOURCES)
    assert "<!-- num:results.test.full.accuracy|rpct1 -->98.7% (76/77)<!-- /num -->" in out
    assert out.endswith("손으로 쓴 98.7% 는 그대로.")
    assert docs.fill_markers(out, SOURCES) == out  # 멱등


def test_formats_and_missing_paths():
    assert docs.fmt(0.1234, "pct1") == "12.3%" and docs.fmt(0.5, "pct0") == "50%" and docs.fmt(0.98765, "f3") == "0.988"
    assert docs.fmt({"k": 3, "n": 9}, "kn") == "3/9"
    with pytest.raises(KeyError):
        docs.fill_markers("<!-- num:results.nope.x|pct1 -->x<!-- /num -->", SOURCES)
    with pytest.raises(ValueError):
        docs.fmt(1, "weird")


def test_check_mode_flags_drift_and_generate_fixes_it(tmp_path):
    root = tmp_path
    (root / "artifacts").mkdir()
    metrics = {
        "n": 4, "gold_counts": {"APPROVE": 2, "REVIEW": 1, "REJECT": 1}, "pred_counts": {"APPROVE": 2, "REVIEW": 1, "REJECT": 1},
        "confusion": {"APPROVE": {"APPROVE": 2, "REVIEW": 0, "REJECT": 0}, "REVIEW": {"APPROVE": 0, "REVIEW": 1, "REJECT": 0},
                      "REJECT": {"APPROVE": 0, "REVIEW": 0, "REJECT": 1}},
        "accuracy": {"k": 4, "n": 4, "value": 1.0, "ci": [0.51, 1.0]}, "macro_f1": 1.0,
        "hard_pass_through": {"k": 0, "n": 1, "value": 0.0, "ci": [0.0, 0.79]}, "unsafe_approve": {"k": 0, "n": 2, "value": 0.0, "ci": [0.0, 0.66]},
        "false_reject": {"k": 0, "n": 2, "value": 0.0, "ci": [0.0, 0.66]}, "over_review": {"k": 0, "n": 2, "value": 0.0, "ci": [0.0, 0.66]},
        "downgrade": {"k": 0, "n": 1, "value": 0.0, "ci": [0.0, 0.79]}, "auto_rate": {"k": 3, "n": 4, "value": 0.75, "ci": [0.3, 0.95]},
        "auto_precision": {"k": 3, "n": 3, "value": 1.0, "ci": [0.44, 1.0]},
        "rule": {"micro": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "tp": 2, "fp": 0, "fn": 0}, "per_rule": {}},
        "localization": {"k": 2, "n": 2, "value": 1.0, "ci": [0.34, 1.0]}, "fin_missing_exact": {"k": 0, "n": 0, "value": None, "ci": [0.0, 1.0]},
        "grounding": {"k": 2, "n": 2, "value": 1.0, "ci": [0.34, 1.0]}, "latency_ms": {"p50": 0.1, "p95": 0.2}, "slices": {},
    }
    art = {"meta": {"git_sha": "abc1234", "dirty": False, "created": "2026-10-11 00:00:00Z", "python": "3.12", "datasets": {}},
           "results": {"test": {"full": metrics, "B1_blocklist": metrics}}}
    (root / "artifacts/results.json").write_text(json.dumps(art), encoding="utf-8")
    (root / "README.md").write_text("정확도 <!-- num:results.test.full.accuracy.value|pct1 -->0<!-- /num -->\n", encoding="utf-8")
    assert main(["docs", "--root", str(root), "--check"]) == 1       # 아직 생성 전: 불일치
    assert main(["docs", "--root", str(root)]) == 0
    assert "100.0%" in (root / "README.md").read_text(encoding="utf-8")
    assert (root / "docs/RESULTS.md").read_text(encoding="utf-8").startswith("# 결과 (자동 생성)")
    assert main(["docs", "--root", str(root), "--check"]) == 0
    (root / "README.md").write_text("정확도 <!-- num:results.test.full.accuracy.value|pct1 -->50.0%<!-- /num -->\n", encoding="utf-8")
    assert main(["docs", "--root", str(root), "--check"]) == 1       # 손으로 고친 수치는 잡힌다


# --------------------------------------------------------------------------- 문서가 말하는 id 목록은 산출물과 같아야 한다
ROOT = Path(__file__).resolve().parents[1]


def _doc_ids(name: str) -> set[str]:
    text = (ROOT / "docs" / "FAILURES.md").read_text(encoding="utf-8")
    m = re.search(rf"<!-- ids:{name} -->(.*?)<!-- /ids -->", text, re.DOTALL)
    assert m, f"FAILURES.md 에 ids:{name} 표식이 없다"
    return set(re.findall(r"R-\d{3}", m.group(1)))


def _unsafe_ids(fname: str) -> set[str]:
    errors = json.loads((ROOT / "artifacts" / "history" / fname).read_text(encoding="utf-8"))["redteam"]
    return {e["id"] for e in errors if e["pred"] == "APPROVE" and e["gold"] != "APPROVE"}


def test_failures_doc_lists_exactly_the_ids_the_first_contact_approved():
    """F-10 이 '고친 7건 + 고치지 않은 패러프레이즈 14건' 으로 나눈 id 가 첫 접촉의 위험 승인 21건과 정확히 같다."""
    fixed, paraphrase = _doc_ids("fixed_v0"), _doc_ids("paraphrase_v0")
    assert not fixed & paraphrase and len(fixed) == 7 and len(paraphrase) == 14
    assert fixed | paraphrase == _unsafe_ids("redteam_first_contact_errors.json")


def test_after_the_fixes_the_remaining_unsafe_approvals_are_exactly_the_paraphrases():
    assert _unsafe_ids("redteam_after_fixes_errors.json") == _doc_ids("paraphrase_v0")
