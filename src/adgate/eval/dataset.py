"""평가셋 로더와 검증. 정답 라벨 자체의 일관성도 여기서 검사한다."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..models import Creative, Outcome
from ..policy import Policy, load_policy


class GoldViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    field: str | None = None
    quote: str | None = None
    missing: list[str] | None = None


class Gold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Outcome
    violations: list[GoldViolation] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    creative: Creative
    gold: Gold


def load(path: str | Path) -> list[Example]:
    out: list[Example] = []
    with Path(path).open(encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                out.append(Example.model_validate(json.loads(line)))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"{path}:{n}: {exc}") from exc
    return out


_ORDER = {"APPROVE": 0, "REVIEW": 1, "REJECT": 2}
_SEV_TO_OUTCOME = {"info": 0, "review": 1, "reject": 2}


# 랜딩 페이지(`landing_text`)에서 찾아도 사람 검토까지만 가는 규칙(POLICY.md 2절).
LANDING_REVIEW_RULES = frozenset({"AD-GUA-01", "AD-HLT-01", "AD-BRD-01"})


def validate(examples: list[Example], policy: Policy | None = None) -> list[str]:
    """라벨 일관성 오류 목록. 비어 있어야 한다."""
    policy = policy or load_policy()
    errors: list[str] = []
    seen: set[str] = set()
    for ex in examples:
        if ex.id in seen:
            errors.append(f"{ex.id}: 중복 id")
        seen.add(ex.id)
        lo = hi = 0
        for v in ex.gold.violations:
            rule = policy.rules.get(v.rule_id)
            if rule is None:
                errors.append(f"{ex.id}: 알 수 없는 규칙 {v.rule_id}")
                continue
            sevs = [_SEV_TO_OUTCOME[c.severity] for c in rule.codes.values() if c.severity != "info"]
            if v.field == "landing_text" and v.rule_id in LANDING_REVIEW_RULES:
                sevs = [_ORDER["REVIEW"]]  # POLICY 2절: 랜딩에서 찾은 보장·질병 효능·제한 주제 표현은 REVIEW
            lo, hi = max(lo, min(sevs)), max(hi, max(sevs))
            if v.field is not None:
                text = ex.creative.text(v.field)
                if v.quote is None or v.quote not in text:
                    errors.append(f"{ex.id}: 인용 {v.quote!r} 이(가) {v.field} 에 없음")
            elif v.quote is not None:
                errors.append(f"{ex.id}: field 없이 quote 가 있음")
        d = _ORDER[ex.gold.decision]
        if not ex.gold.violations and d != 0:
            errors.append(f"{ex.id}: 위반이 없는데 {ex.gold.decision}")
        if ex.gold.violations and not (lo <= d <= hi):
            errors.append(f"{ex.id}: 판정 {ex.gold.decision} 이(가) 위반 등급 범위 밖")
    return errors


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_manifest(manifest: str | Path, base: str | Path | None = None) -> dict[str, bool]:
    """MANIFEST.sha256 (sha256sum 형식)을 검사한다. 파일별 일치 여부."""
    manifest = Path(manifest)
    base = Path(base) if base else manifest.parent
    out: dict[str, bool] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        target = base / name.strip()
        out[name.strip()] = target.exists() and sha256_file(target) == digest  # 없는 파일은 '일치하지 않음'이지 예외가 아니다
    return out
