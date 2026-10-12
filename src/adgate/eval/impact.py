"""엔진 변경의 소재 단위 영향.

지표가 같아도 *어느 소재*의 판정이 바뀌었는지, 바뀐 것이 정답 쪽인지 반대쪽인지는 지표에 안 보인다. 심사 엔진(어휘·예외·규칙)을 고칠 때마다 변경 전·후에
같은 소재 묶음의 판정과 발견을 떠서(``snapshot``) 견주고(``diff``), 정답에서 *멀어진* 소재(새 오판)가 0인지 확인한다.

변경 전 스냅샷은 변경 전 리비전의 작업트리에서 이 모듈을 불러 뜬다(``git worktree add``). 이 모듈은 평가셋 파일과 엔진의 공개 진입점
(``review``, 데이터 로더)만 쓰므로 그렇게 해도 된다. 소재 묶음: dev·test·redteam·gray, 그리고 이미지 셋의 두 입력(정답 글자를 준 것·안 준 것).
읽기만 한다 — 동결 파일을 바꾸지 않고, 측정 기록(MEASUREMENTS.md)에도 줄을 더하지 않는다. 이 스냅샷은 *측정이 아니라 변경 점검*이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import ReviewConfig
from ..pipeline import review
from ..vision.dataset import load_vision
from .dataset import load
from .report import git_state, now

SETS = ("dev", "test", "redteam", "gray")
_ORDER = {"APPROVE": 0, "REVIEW": 1, "REJECT": 2}


def _record(gold: str, result: Any) -> dict[str, Any]:
    findings = sorted([f.rule_id, f.code, f.field, f.quote] for f in result.findings if f.quote)
    return {"gold": gold, "pred": result.outcome, "findings": findings}


def snapshot(root: Path) -> dict[str, Any]:
    """``root`` 의 평가셋 전부에 기본 설정 엔진을 돌려 소재마다 (정답, 판정, 인용이 있는 발견)을 모은다."""
    cfg = ReviewConfig()
    records: dict[str, dict[str, Any]] = {}
    sizes: dict[str, int] = {}
    for name in SETS:
        examples = load(root / "data" / f"{name}.jsonl")
        sizes[name] = len(examples)
        for ex in examples:
            records[f"{name}:{ex.id}"] = _record(ex.gold.decision, review(ex.creative, cfg))
    items = load_vision(root / "data" / "vision" / "vision.jsonl")
    sizes["vision"] = len(items)
    for it in items:
        for arm, ex in (("vision_oracle", it.oracle()), ("vision_noimage", it.example(None))):
            records[f"{arm}:{it.id}"] = _record(ex.gold.decision, review(ex.creative, cfg))
    sha, dirty = git_state(root)
    return {"meta": {"git_sha": sha, "dirty": dirty, "created": now(), "sets": sizes}, "records": records}


def _direction(gold: str, before: str, after: str) -> str:
    """판정이 바뀐 소재가 정답 쪽으로 갔는가(toward), 멀어졌는가(away), 정답과의 거리가 같은가(neutral)."""
    g, b, a = _ORDER[gold], _ORDER[before], _ORDER[after]
    if abs(a - g) < abs(b - g):
        return "toward"
    if abs(a - g) > abs(b - g):
        return "away"
    return "neutral"


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """두 스냅샷을 소재 단위로 견준다. 키 집합이 다르면(소재가 늘거나 줄었으면) 견줄 수 없으므로 거부한다."""
    b, a = before["records"], after["records"]
    if set(b) != set(a):
        raise ValueError(f"소재 묶음이 다르다: 앞에만 {sorted(set(b) - set(a))[:3]}, 뒤에만 {sorted(set(a) - set(b))[:3]}")
    changed: list[dict[str, Any]] = []
    for key in sorted(b):
        rb, ra = b[key], a[key]
        if rb["gold"] != ra["gold"]:
            raise ValueError(f"{key}: 정답이 다르다(동결 파일이 바뀌었다)")
        if rb == ra:
            continue
        old_f, new_f = {tuple(x) for x in rb["findings"]}, {tuple(x) for x in ra["findings"]}
        changed.append({
            "key": key, "gold": ra["gold"], "before": rb["pred"], "after": ra["pred"],
            "direction": _direction(ra["gold"], rb["pred"], ra["pred"]) if rb["pred"] != ra["pred"] else "same_decision",
            "added": sorted(list(x) for x in new_f - old_f), "removed": sorted(list(x) for x in old_f - new_f),
        })
    flips = [c for c in changed if c["direction"] != "same_decision"]
    per_set: dict[str, dict[str, int]] = {}
    for key in b:
        s = key.split(":")[0]
        per_set.setdefault(s, {"records": 0, "changed": 0, "decision_flips": 0, "toward": 0, "away": 0})
        per_set[s]["records"] += 1
    for c in changed:
        s = c["key"].split(":")[0]
        per_set[s]["changed"] += 1
        if c["direction"] != "same_decision":
            per_set[s]["decision_flips"] += 1
            if c["direction"] in ("toward", "away"):
                per_set[s][c["direction"]] += 1
    summary = {
        "records": len(b), "changed": len(changed), "decision_flips": len(flips),
        "toward_gold": sum(c["direction"] == "toward" for c in flips), "away_from_gold": sum(c["direction"] == "away" for c in flips),
        "fixed": sum(c["after"] == c["gold"] != c["before"] for c in flips),         # 틀리던 소재가 맞게 됐다
        "new_errors": sum(c["before"] == c["gold"] != c["after"] for c in flips),    # 맞던 소재가 틀리게 됐다 — 0 이어야 한다
        "per_set": per_set,
    }
    return {"meta": {"before": before["meta"], "after": after["meta"]}, "summary": summary, "changed": changed}
