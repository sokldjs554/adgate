"""발견을 광고주에게 보여 줄 사유로 바꾼다. 문구는 정책 파일에서 가져온다(템플릿, 생성형 아님)."""

from __future__ import annotations

from .models import Finding, Reason
from .policy import Policy


def _title(policy: Policy, rule_id: str) -> str:
    rule = policy.rules.get(rule_id)
    return rule.title if rule is not None else "의미 검사"


def explain(findings: list[Finding], policy: Policy) -> list[Reason]:
    reasons: list[Reason] = []
    for f in findings:
        if f.severity == "info":
            continue
        spec = policy.spec(f.rule_id, f.code)
        title = _title(policy, f.rule_id)
        if f.rule_id == "AD-FIN-01" and f.missing:
            catalog = policy.rule("AD-FIN-01").missing
            for key in f.missing:
                item = catalog[key]
                reasons.append(
                    Reason(
                        rule_id=f.rule_id, code=f"{f.code}:{key}", severity=f.severity, field=None, quote=None,
                        title=title, message=f"필수 고지가 빠져 있습니다: {item['label']}", fix=item["text"],
                    )
                )
            continue
        message = spec.message
        if f.note and f.code == "loose_match":
            message = f"{message} ({f.note})"
        reasons.append(
            Reason(
                rule_id=f.rule_id, code=f.code, severity=f.severity, field=f.field, quote=f.quote,
                title=title, message=message, fix=spec.fix,
            )
        )
    order = {"reject": 0, "review": 1, "info": 2}
    reasons.sort(key=lambda r: order[r.severity])
    return reasons
