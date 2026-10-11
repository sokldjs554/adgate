"""판정. 발견의 가장 높은 등급으로 결정한다(reject > review > approve). LLM 은 여기에 관여하지 못한다."""

from __future__ import annotations

from .models import Finding, Outcome


def decide(findings: list[Finding]) -> Outcome:
    severities = {f.severity for f in findings}
    if "reject" in severities:
        return "REJECT"
    if "review" in severities:
        return "REVIEW"
    return "APPROVE"
