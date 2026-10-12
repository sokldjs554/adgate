"""엔진 변경의 소재 단위 영향(adgate.eval.impact): 스냅샷은 결정론이고, diff 는 정답 쪽·반대쪽을 가른다."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from adgate.eval.impact import diff, snapshot

ROOT = Path(__file__).resolve().parents[1]


def snap(records: dict[str, tuple[str, str, list[list[str]]]]) -> dict:
    return {"meta": {"git_sha": "x", "dirty": False}, "records": {k: {"gold": g, "pred": p, "findings": f} for k, (g, p, f) in records.items()}}


def test_snapshot_covers_every_set_and_is_deterministic() -> None:
    a, b = snapshot(ROOT), snapshot(ROOT)
    assert a["records"] == b["records"]
    sizes = a["meta"]["sets"]
    assert set(sizes) == {"dev", "test", "redteam", "gray", "vision"}
    assert len(a["records"]) == sizes["dev"] + sizes["test"] + sizes["redteam"] + sizes["gray"] + 2 * sizes["vision"]
    assert all(r["pred"] in ("APPROVE", "REVIEW", "REJECT") for r in a["records"].values())


def test_diff_of_a_snapshot_with_itself_is_empty() -> None:
    a = snapshot(ROOT)
    d = diff(a, copy.deepcopy(a))
    assert d["changed"] == [] and d["summary"]["new_errors"] == 0 and d["summary"]["decision_flips"] == 0


def test_diff_separates_fixed_new_errors_and_findings_only_changes() -> None:
    f = [["AD-GUA-01", "guarantee", "body", "수익 보장"]]
    before = snap({
        "s:fix": ("REJECT", "APPROVE", []),                 # 틀리던 것
        "s:break": ("APPROVE", "APPROVE", []),             # 맞던 것
        "s:more": ("APPROVE", "REVIEW", []),               # 틀린 채로 더 틀려짐
        "s:same": ("REJECT", "REJECT", f),
        "s:find": ("REJECT", "REJECT", []),
    })
    after = snap({
        "s:fix": ("REJECT", "REJECT", f),
        "s:break": ("APPROVE", "REJECT", f),
        "s:more": ("APPROVE", "REJECT", f),
        "s:same": ("REJECT", "REJECT", f),
        "s:find": ("REJECT", "REJECT", f),                 # 판정은 같고 발견만 늘었다
    })
    d = diff(before, after)
    by = {c["key"]: c for c in d["changed"]}
    assert set(by) == {"s:fix", "s:break", "s:more", "s:find"}
    assert by["s:fix"]["direction"] == "toward" and by["s:break"]["direction"] == "away" and by["s:more"]["direction"] == "away"
    assert by["s:find"]["direction"] == "same_decision" and by["s:find"]["added"] == [f[0]]
    sm = d["summary"]
    assert (sm["changed"], sm["decision_flips"], sm["toward_gold"], sm["away_from_gold"], sm["fixed"], sm["new_errors"]) == (4, 3, 1, 2, 1, 1)
    assert sm["per_set"]["s"]["records"] == 5


def test_a_review_to_approve_flip_on_a_reject_item_moves_away_even_though_both_are_wrong() -> None:
    d = diff(snap({"s:x": ("REJECT", "REVIEW", [])}), snap({"s:x": ("REJECT", "APPROVE", [])}))
    assert d["changed"][0]["direction"] == "away" and d["summary"]["new_errors"] == 0   # 이미 틀렸으므로 '새 오판'은 아니지만 정답에서 멀어졌다


def test_diff_refuses_incomparable_snapshots() -> None:
    with pytest.raises(ValueError, match="소재 묶음"):
        diff(snap({"s:a": ("REJECT", "REJECT", [])}), snap({"s:b": ("REJECT", "REJECT", [])}))
    with pytest.raises(ValueError, match="정답이 다르다"):
        diff(snap({"s:a": ("REJECT", "REJECT", [])}), snap({"s:a": ("APPROVE", "REJECT", [])}))
