from __future__ import annotations

from adgate.policy import load_policy
from adgate.rules import TEXT_DETECTORS


def test_priority_rules_exist_and_codes_are_well_formed():
    p = load_policy()
    assert p.version == "v1"
    for rid in p.priority:
        assert rid in p.rules
    for rid, rule in p.rules.items():
        assert rule.title
        assert rule.codes, rid
        for code, spec in rule.codes.items():
            assert spec.severity in {"reject", "review", "info"}, (rid, code)
            assert spec.message and spec.fix, (rid, code)


def test_every_text_detector_has_a_policy_rule():
    p = load_policy()
    for rid, _ in TEXT_DETECTORS:
        assert rid in p.rules


def test_fin_missing_catalog_covers_all_disclosures():
    p = load_policy()
    assert set(p.rule("AD-FIN-01").missing) == {
        "credit_warning", "rate_info", "principal_loss_warning", "past_performance_notice",
    }


def test_common_codes_are_review_grade():
    p = load_policy()
    assert p.spec("SYS-JUDGE", "judge_unavailable").severity == "review"
    assert p.spec("AD-GUA-01", "loose_match").severity == "review"
