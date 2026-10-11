"""정책 메타데이터 로더. 규칙 ID·등급·안내 문구의 단일 출처는 policy.v1.yaml 이다."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from .models import Severity


@dataclass(frozen=True)
class CodeSpec:
    severity: Severity
    message: str
    fix: str


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    title: str
    applies_to: tuple[str, ...] | None  # None 이면 모든 업종
    codes: dict[str, CodeSpec]
    missing: dict[str, dict[str, str]]


@dataclass(frozen=True)
class Policy:
    version: str
    priority: tuple[str, ...]
    rules: dict[str, RuleSpec]
    common_codes: dict[str, CodeSpec]

    def rule(self, rule_id: str) -> RuleSpec:
        return self.rules[rule_id]

    def spec(self, rule_id: str, code: str) -> CodeSpec:
        rule = self.rules.get(rule_id)
        if rule is not None and code in rule.codes:
            return rule.codes[code]
        if code in self.common_codes:
            return self.common_codes[code]
        raise KeyError(f"unknown code {rule_id}/{code}")

    def has_code(self, rule_id: str, code: str) -> bool:
        rule = self.rules.get(rule_id)
        return (rule is not None and code in rule.codes) or code in self.common_codes

    def applies(self, rule_id: str, category: str) -> bool:
        applies_to = self.rules[rule_id].applies_to
        return applies_to is None or category in applies_to

    def rank(self, rule_id: str) -> int:
        """우선순위 숫자(작을수록 높음). 목록에 없는 규칙은 맨 뒤."""
        try:
            return self.priority.index(rule_id)
        except ValueError:
            return len(self.priority)


def _codes(raw: dict[str, Any]) -> dict[str, CodeSpec]:
    return {
        name: CodeSpec(severity=spec["severity"], message=spec["message"], fix=spec["fix"])
        for name, spec in raw.items()
    }


def parse(data: dict[str, Any]) -> Policy:
    rules: dict[str, RuleSpec] = {}
    for rule_id, raw in data["rules"].items():
        applies = raw.get("applies_to")
        rules[rule_id] = RuleSpec(
            rule_id=rule_id,
            title=raw["title"],
            applies_to=tuple(applies) if applies else None,
            codes=_codes(raw["codes"]),
            missing=raw.get("missing", {}),
        )
    policy = Policy(
        version=data["version"],
        priority=tuple(data["priority"]),
        rules=rules,
        common_codes=_codes(data.get("common_codes", {})),
    )
    for rule_id in policy.priority:
        if rule_id not in policy.rules:
            raise ValueError(f"priority lists unknown rule {rule_id}")
    return policy


@lru_cache(maxsize=1)
def load_policy() -> Policy:
    text = resources.files("adgate").joinpath("data/policy.v1.yaml").read_text(encoding="utf-8")
    return parse(yaml.safe_load(text))
