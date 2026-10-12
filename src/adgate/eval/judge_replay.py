"""녹음 재생 Judge 측정의 요약. 사전등록(docs/JUDGE_REPLAY.md 3절)이 정한 양을 산출물에서 그대로 계산한다.

세 구성 — ``full``(결정론), ``full+judge``(Judge 발견이 정책 등급 그대로), ``full+judge_cap``(Judge 발견은 REVIEW 까지만) — 의
*같은 소재별* 예측을 짝지어 본다. 집계 지표(정확도 등)만으로는 보이지 않는 것이 있기 때문이다.

* **전이**: 정답별로 (결정론 판정 → Judge 판정) 쌍의 개수. 정답 APPROVE 가 어디로 가는지가 E2 와 판단 규칙이다.
  집계 지표만 보면 REVIEW → REJECT 로 옮겨 간 소재가 합계에서 사라진다.
* **결정론 발견 손실(E3)**: ``full`` 의 발견이 ``full+judge`` 에서 사라진 소재 수. 병합이 Judge 발견만 더하므로 구조상 0 이다.
* **근거 없는 의심(E4)**: ``judge_ungrounded`` 에스컬레이션이 붙은 소재 수와 버려진 의심 수.
* **Judge 가 바꾼 소재 목록**: 정답과 함께 보여 준다. 숫자만 있으면 어느 소재 때문인지 검증할 수 없다.

패러프레이즈 슬라이스(E1)는 지표의 슬라이스에서 읽는다. 여기서 다시 계산하지 않는다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..models import Finding
from .dataset import Example
from .metrics import OUTCOMES, ratio
from .systems import Prediction

_RANK = {"APPROVE": 0, "REVIEW": 1, "REJECT": 2}


def _deterministic(findings: list[Finding]) -> set[tuple[Any, ...]]:
    return {(f.rule_id, f.field, f.start, f.end, f.severity) for f in findings if f.source != "llm"}


def _llm(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.source == "llm"]


def summarize(
    examples: list[Example],
    base: list[Prediction],
    judge: list[Prediction],
    cap: list[Prediction],
) -> dict[str, Any]:
    """``examples`` 와 같은 순서의 예측 세 벌을 받아 사전등록 항목을 계산한다."""
    assert len(examples) == len(base) == len(judge) == len(cap)
    n = len(examples)
    transitions: dict[str, Counter[str]] = {g: Counter() for g in OUTCOMES}
    transitions_cap: dict[str, Counter[str]] = {g: Counter() for g in OUTCOMES}
    changed: list[dict[str, Any]] = []
    lost = 0
    with_llm = ungrounded = unavailable = dropped = n_llm = 0
    unavailable_ids: list[str] = []
    ungrounded_ids: list[str] = []
    for ex, b, j, c in zip(examples, base, judge, cap, strict=True):
        g = ex.gold.decision
        transitions[g][f"{b.outcome}→{j.outcome}"] += 1
        transitions_cap[g][f"{b.outcome}→{c.outcome}"] += 1
        lost += bool(_deterministic(b.findings) - _deterministic(j.findings))
        mine = _llm(j.findings)
        n_llm += len(mine)
        with_llm += bool(mine)
        ungrounded += "judge_ungrounded" in j.escalations
        unavailable += "judge_unavailable" in j.escalations
        if "judge_ungrounded" in j.escalations:
            ungrounded_ids.append(ex.id)
        if "judge_unavailable" in j.escalations:
            unavailable_ids.append(ex.id)
        dropped += j.dropped_ungrounded
        if _RANK[j.outcome] != _RANK[b.outcome]:
            changed.append(
                {
                    "id": ex.id,
                    "gold": g,
                    "tags": list(ex.gold.tags),
                    "full": b.outcome,
                    "judge": j.outcome,
                    "judge_cap": c.outcome,
                    "llm_rules": sorted({f.rule_id for f in mine}),
                    "ungrounded": "judge_ungrounded" in j.escalations,
                }
            )
    approve = [x for x in changed if x["gold"] == "APPROVE"]
    return {
        "n": n,
        "transitions": {g: dict(sorted(t.items())) for g, t in transitions.items()},
        "transitions_cap": {g: dict(sorted(t.items())) for g, t in transitions_cap.items()},
        "judge_added": ratio(with_llm, n),
        "judge_findings": n_llm,
        "judge_ungrounded": ratio(ungrounded, n),
        "judge_unavailable": ratio(unavailable, n),
        "unavailable_ids": unavailable_ids,  # 호출 실패로 REVIEW 가 된 소재(실호출 측정의 L1·L7)
        "ungrounded_ids": ungrounded_ids,    # 인용이 원문과 맞지 않아 버려진 의심이 있는 소재(L4)
        "dropped_ungrounded": dropped,
        "deterministic_lost": lost,
        "changed": changed,
        "approve_gold_changed": approve,
        "approve_gold_to_reject": [x["id"] for x in approve if x["judge"] == "REJECT"],
        "unsafe_fixed": [x["id"] for x in changed if x["gold"] != "APPROVE" and x["full"] == "APPROVE" and x["judge"] != "APPROVE"],
    }


def compare_recordings(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """두 녹음(열쇠 → ``{"response"}`` 또는 ``{"error"}`` 행)을 같은 소재끼리 견준다. a 는 대역, b 는 실호출처럼 쓴다.

    * ``agree_empty`` / ``agree_nonempty``: 둘 다 후보를 내지 않았다 / 둘 다 냈다.
    * ``only_a`` / ``only_b``: 한쪽만 후보를 냈다.
    * ``error_a`` / ``error_b``: 그쪽 호출이 실패했다(후보 없음으로 세지 않는다).
    * ``rule_sets_equal``: 둘 다 후보를 낸 소재 중 낸 규칙 집합이 같은 수.
    """
    keys = sorted(set(a) & set(b))
    out: dict[str, Any] = {"n": len(keys), "agree_empty": 0, "agree_nonempty": 0, "only_a": 0, "only_b": 0, "error_a": 0, "error_b": 0,
           "both_nonempty": 0, "rule_sets_equal": 0, "only_in_a": len(set(a) - set(b)), "only_in_b": len(set(b) - set(a))}
    for k in keys:
        ra, rb = a[k], b[k]
        if "error" in ra or "error" in rb:
            out["error_a"] += "error" in ra
            out["error_b"] += "error" in rb
            continue
        fa, fb = ra["response"]["findings"], rb["response"]["findings"]
        if not fa and not fb:
            out["agree_empty"] += 1
        elif fa and fb:
            out["agree_nonempty"] += 1
            out["both_nonempty"] += 1
            out["rule_sets_equal"] += {f["rule_id"] for f in fa} == {f["rule_id"] for f in fb}
        elif fa:
            out["only_a"] += 1
        else:
            out["only_b"] += 1
    compared = out["agree_empty"] + out["agree_nonempty"] + out["only_a"] + out["only_b"]
    out["compared"] = compared
    out["agreement"] = ratio(out["agree_empty"] + out["agree_nonempty"], compared)
    return out
