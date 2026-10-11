from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import mk

from adgate.cli import main
from adgate.eval import agreement, dataset, gate, metrics
from adgate.eval import gen as generator
from adgate.eval.dataset import Example, Gold, GoldViolation
from adgate.eval.systems import Prediction, all_to_human, naive_blocklist, pipeline_system
from adgate.models import Finding

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def ex(i: str, decision: str, violations=(), **fields) -> Example:
    return Example(id=i, creative=mk(**fields), gold=Gold(decision=decision, violations=list(violations)))  # type: ignore[arg-type]


def pred(outcome: str, *findings: Finding) -> Prediction:
    return Prediction(outcome, list(findings), 1.0)  # type: ignore[arg-type]


def finding(rule: str, field: str, text: str, quote: str, sev: str = "reject") -> Finding:
    i = text.index(quote)
    return Finding(rule_id=rule, code="x", severity=sev, field=field, quote=quote, start=i, end=i + len(quote))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- 지표
def test_wilson_interval_basics():
    assert metrics.wilson(0, 0) == (0.0, 1.0)
    lo, hi = metrics.wilson(0, 20)
    assert lo == 0.0 and 0.1 < hi < 0.2
    lo, hi = metrics.wilson(10, 20)
    assert lo < 0.5 < hi


def test_metrics_on_a_hand_computed_example():
    v = lambda r, f, q: GoldViolation(rule_id=r, field=f, quote=q)  # noqa: E731
    examples = [
        ex("1", "REJECT", [v("AD-GUA-01", "headline", "무조건 승인")], headline="무조건 승인"),
        ex("2", "REJECT", [v("AD-GUA-01", "headline", "수익 보장")], headline="수익 보장"),
        ex("3", "REVIEW", [v("AD-URG-01", "headline", "오늘만")], headline="오늘만 특가"),
        ex("4", "APPROVE", headline="좋은 앱"),
        ex("5", "APPROVE", headline="좋은 앱 2"),
    ]
    preds = [
        pred("REJECT", finding("AD-GUA-01", "headline", "무조건 승인", "무조건")),   # 정답, 인용 겹침
        pred("APPROVE"),                                                        # 위험: 반려해야 할 것을 승인
        pred("REVIEW", finding("AD-URG-01", "headline", "오늘만 특가", "오늘만", "review")),
        pred("REJECT", finding("AD-ABS-01", "headline", "좋은 앱", "좋은")),        # 정상 소재를 반려
        pred("APPROVE"),
    ]
    m = metrics.compute(examples, preds)
    assert m["hard_pass_through"]["k"] == 1 and m["hard_pass_through"]["n"] == 2
    assert m["unsafe_approve"]["k"] == 1 and m["unsafe_approve"]["n"] == 3
    assert m["false_reject"]["k"] == 1 and m["false_reject"]["n"] == 2
    assert m["over_review"]["k"] == 0
    assert m["auto_rate"]["k"] == 4 and m["auto_precision"]["k"] == 2          # 자동 판정 4건 중 2건이 맞다
    assert m["accuracy"]["k"] == 3
    r = m["rule"]["micro"]
    assert (r["tp"], r["fp"], r["fn"]) == (2, 1, 1)
    assert m["localization"]["k"] == 2 and m["localization"]["n"] == 2
    assert m["grounding"]["k"] == 3 and m["grounding"]["n"] == 3


def test_localization_requires_overlap_in_the_same_field():
    e = ex("1", "REJECT", [GoldViolation(rule_id="AD-GUA-01", field="headline", quote="무조건 승인")],
           headline="무조건 승인", body="무조건 승인")
    wrong_field = pred("REJECT", finding("AD-GUA-01", "body", "무조건 승인", "무조건 승인"))
    assert metrics.compute([e], [wrong_field])["localization"]["k"] == 0
    right = pred("REJECT", finding("AD-GUA-01", "headline", "무조건 승인", "승인"))
    assert metrics.compute([e], [right])["localization"]["k"] == 1


def test_fin_missing_set_must_match_exactly():
    gold = ex("1", "REJECT", [GoldViolation(rule_id="AD-FIN-01", missing=["credit_warning", "rate_info"])], headline="대출", category="loan")
    p_ok = pred("REJECT", Finding(rule_id="AD-FIN-01", code="missing_disclosure", severity="reject", missing=["rate_info", "credit_warning"]))
    p_bad = pred("REJECT", Finding(rule_id="AD-FIN-01", code="missing_disclosure", severity="reject", missing=["credit_warning"]))
    assert metrics.compute([gold], [p_ok])["fin_missing_exact"]["k"] == 1
    assert metrics.compute([gold], [p_bad])["fin_missing_exact"]["k"] == 0


def test_info_findings_and_unknown_rules_do_not_count_as_predictions():
    e = ex("1", "APPROVE", headline="앱")
    p = pred("APPROVE", Finding(rule_id="AD-FMT-01", code="missing_cta", severity="info"), Finding(rule_id="SYS-JUDGE", code="judge_unavailable", severity="review"))
    assert metrics.compute([e], [p])["rule"]["micro"]["fp"] == 0


# --------------------------------------------------------------------------- 시스템·베이스라인
def test_b0_is_safe_but_never_automatic_and_b1_is_naive():
    examples = dataset.load(DATA / "dev.jsonl")
    m0 = metrics.compute(examples, [all_to_human()(e.creative) for e in examples])
    assert m0["unsafe_approve"]["k"] == 0 and m0["auto_rate"]["k"] == 0
    m1 = metrics.compute(examples, [naive_blocklist()(e.creative) for e in examples])
    assert m1["false_reject"]["k"] > 0 and m1["unsafe_approve"]["k"] > 0   # 정상 유사를 막고, 우회·메타데이터는 놓친다
    assert m1["pred_counts"]["REVIEW"] == 0                                  # B1 은 보류가 없다


# --------------------------------------------------------------------------- 데이터셋
def test_committed_datasets_are_consistent_and_frozen():
    for name in ("dev", "test"):
        assert dataset.validate(dataset.load(DATA / f"{name}.jsonl")) == []
    assert all(dataset.verify_manifest(DATA / "MANIFEST.sha256").values())


def test_validate_catches_label_errors():
    bad_quote = ex("1", "REJECT", [GoldViolation(rule_id="AD-GUA-01", field="headline", quote="없는 인용")], headline="앱")
    unknown = ex("2", "REJECT", [GoldViolation(rule_id="AD-XXX-01")], headline="앱")
    approve_with_violation = ex("3", "APPROVE", [GoldViolation(rule_id="AD-GUA-01", field="headline", quote="앱")], headline="앱")
    review_for_reject_only_rule = ex("4", "REVIEW", [GoldViolation(rule_id="AD-GUA-01", field="headline", quote="앱")], headline="앱")
    dup = [ex("5", "APPROVE", headline="a"), ex("5", "APPROVE", headline="b")]
    errs = dataset.validate([bad_quote, unknown, approve_with_violation, review_for_reject_only_rule, *dup])
    joined = "\n".join(errs)
    assert "인용" in joined and "알 수 없는 규칙" in joined and "범위 밖" in joined and "중복 id" in joined


def test_manifest_detects_modification(tmp_path):
    f = tmp_path / "x.jsonl"
    f.write_text("a\n")
    m = tmp_path / "MANIFEST.sha256"
    m.write_text(f"{dataset.sha256_file(f)}  x.jsonl\n")
    assert dataset.verify_manifest(m) == {"x.jsonl": True}
    f.write_text("b\n")
    assert dataset.verify_manifest(m) == {"x.jsonl": False}


# --------------------------------------------------------------------------- 생성기
def test_generator_is_deterministic_and_labels_are_consistent():
    a, b = generator.generate(120, 3), generator.generate(120, 3)
    assert [e.model_dump() for e in a] == [e.model_dump() for e in b]
    assert [e.model_dump() for e in generator.generate(120, 4)] != [e.model_dump() for e in a]
    assert dataset.validate(generator.generate(600, 7)) == []


def test_generator_neutral_pool_has_no_findings():
    for category, rows in generator.NEUTRAL.items():
        for h, body, cta in rows:
            disc = generator.LOAN_DISC if category == "loan" else generator.INV_DISC if category == "invest" else None
            r = pipeline_system()(mk(h, body, cta, disclosure=disc, category=category))
            assert r.outcome == "APPROVE", (category, h, [f.rule_id for f in r.findings])


# --------------------------------------------------------------------------- 일치도
def test_agreement_kappa_and_disagreements():
    examples = [ex(str(i), d, headline="h") for i, d in enumerate(["APPROVE"] * 6 + ["REJECT"] * 4)]
    labels = {e.id: {"id": e.id, "decision": e.gold.decision, "violations": []} for e in examples}
    perfect = agreement.compare(examples, labels)
    assert perfect["kappa"] == 1.0 and perfect["disagreements"] == []
    labels["0"]["decision"] = "REVIEW"
    r = agreement.compare(examples, labels)
    assert r["decision_agreement"]["k"] == 9 and r["kappa"] < 1.0 and r["disagreements"][0]["id"] == "0"
    with pytest.raises(ValueError):
        agreement.compare(examples, {})


# --------------------------------------------------------------------------- 게이트
def test_acceptance_checks_follow_the_preregistered_thresholds():
    examples = dataset.load(DATA / "dev.jsonl")
    m = metrics.compute(examples, [pipeline_system()(e.creative) for e in examples])
    b1 = metrics.compute(examples, [naive_blocklist()(e.creative) for e in examples])
    checks = {c.id: c for c in gate.acceptance(m, b1)}
    assert checks["H1"].ok and checks["H2"].ok and checks["S5"].ok
    worse = json.loads(json.dumps(m))
    worse["hard_pass_through"]["k"] = 1
    assert not {c.id: c for c in gate.acceptance(worse, b1)}["H1"].ok


def test_regression_check_flags_worse_metrics_beyond_tolerance():
    m = {"accuracy": {"value": 0.90}, "unsafe_approve": {"value": 0.05}, "false_reject": {"value": 0.0},
         "over_review": {"value": 0.0}, "auto_rate": {"value": 0.7}, "rule": {"micro": {"f1": 0.9}}}
    base = {"accuracy": 0.95, "unsafe_approve": 0.0, "false_reject": 0.0, "over_review": 0.0, "auto_rate": 0.7, "rule_f1": 0.9}
    bad = {c.id for c in gate.regression(m, base) if not c.ok}
    assert bad == {"R:accuracy", "R:unsafe_approve"}
    assert not gate.failed(gate.regression(m, {**base, "accuracy": 0.9, "unsafe_approve": 0.05}))


def test_sensitivity_flags_components_the_eval_data_cannot_see():
    datasets = {n: dataset.load(DATA / f"{n}.jsonl") for n in ("dev", "test")}
    base = {n: gate.snapshot(metrics.compute(e, [pipeline_system()(x.creative) for x in e])) for n, e in datasets.items()}
    sens = gate.sensitivity(datasets, base)
    assert set(sens) == {"normalize", "exceptions", "challenge", "landing"}
    # 구성요소를 끄면 게이트가 실패해야 한다 — 네 개 모두. 하나라도 못 잡으면 평가 데이터가 그 구성요소를 덮지 못하는 것이다(FAILURES F-5).
    assert all(info["caught"] for info in sens.values()), {k: v["caught"] for k, v in sens.items()}


# --------------------------------------------------------------------------- CLI 프로토콜
def _copy_data(tmp: Path) -> Path:
    d = tmp / "data"
    d.mkdir()
    for n in ("dev.jsonl", "test.jsonl", "MANIFEST.sha256"):
        (d / n).write_bytes((DATA / n).read_bytes())
    return d


def test_cli_refuses_to_measure_test_without_the_flag(tmp_path):
    d = _copy_data(tmp_path)
    with pytest.raises(SystemExit, match="--measure"):
        main(["eval", "--datasets", "test", "--data-dir", str(d), "--out", str(tmp_path / "o.json"), "--errors-out", str(tmp_path / "e.json"),
              "--measurements", str(tmp_path / "M.md")])
    assert not (tmp_path / "M.md").exists()


def test_cli_logs_every_test_measurement_automatically(tmp_path, capsys):
    d = _copy_data(tmp_path)
    args = ["eval", "--datasets", "test", "--systems", "full", "--measure", "--data-dir", str(d), "--out", str(tmp_path / "o.json"),
            "--errors-out", str(tmp_path / "e.json"), "--measurements", str(tmp_path / "M.md")]
    assert main([*args, "--note", "첫 번째"]) == 0
    assert main([*args, "--note", "두 번째"]) == 0
    rows = [ln for ln in (tmp_path / "M.md").read_text().splitlines() if ln.startswith("| 20")]
    assert len(rows) == 2 and "첫 번째" in rows[0] and "두 번째" in rows[1]


def test_cli_refuses_a_modified_frozen_test_set(tmp_path):
    d = _copy_data(tmp_path)
    with (d / "test.jsonl").open("a", encoding="utf-8") as f:
        f.write("\n")
    with pytest.raises(SystemExit, match="동결"):
        main(["eval", "--datasets", "test", "--measure", "--data-dir", str(d), "--out", str(tmp_path / "o.json"),
              "--errors-out", str(tmp_path / "e.json"), "--measurements", str(tmp_path / "M.md")])


def test_cli_review_prints_reasons(tmp_path, capsys):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(mk("무조건 승인", category="loan").model_dump(), ensure_ascii=False))
    assert main(["review", str(f)]) == 0
    out = capsys.readouterr().out
    assert "[REJECT]" in out and "AD-GUA-01" in out and "수정:" in out


def test_validate_command_reports_and_drops_invalid_lines(tmp_path, capsys):
    good = ex("G", "APPROVE", headline="앱").model_dump_json()
    bad_quote = ex("B", "REJECT", [GoldViolation(rule_id="AD-GUA-01", field="headline", quote="없는 인용")], headline="앱").model_dump_json()
    f = tmp_path / "x.jsonl"
    f.write_text("\n".join([good, bad_quote, "not json"]) + "\n", encoding="utf-8")
    out = tmp_path / "ok.jsonl"
    assert main(["validate", str(f)]) == 1
    assert main(["validate", str(f), "--drop-invalid", "--out", str(out)]) == 1
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1 and "\"G\"" in out.read_text(encoding="utf-8")
    assert main(["validate", str(out)]) == 0


# --------------------------------------------------------------------------- 게이트 종료 코드 (CI 가 의존한다)
def test_gate_exit_codes_follow_required_checks_and_regressions(tmp_path, capsys):
    d = _copy_data(tmp_path)
    base = tmp_path / "baseline.json"
    assert main(["gate", "--data-dir", str(d), "--baseline", str(base), "--update-baseline"]) == 0
    assert main(["gate", "--data-dir", str(d), "--baseline", str(base)]) == 0      # 같은 코드, 같은 기준선 → 통과
    saved = json.loads(base.read_text(encoding="utf-8"))
    saved["dev"]["accuracy"] = 1.2                                                  # 현재보다 높은 기준선 → 회귀로 보고 실패
    base.write_text(json.dumps(saved), encoding="utf-8")
    assert main(["gate", "--data-dir", str(d), "--baseline", str(base)]) == 1
    assert "R:dev:accuracy" in capsys.readouterr().out


def test_manifest_with_a_missing_file_reports_false_instead_of_raising(tmp_path):
    (tmp_path / "a.jsonl").write_text("x", encoding="utf-8")
    (tmp_path / "MANIFEST.sha256").write_text(
        f"{dataset.sha256_file(tmp_path / 'a.jsonl')}  a.jsonl\n{'0' * 64}  gone.jsonl\n", encoding="utf-8"
    )
    assert dataset.verify_manifest(tmp_path / "MANIFEST.sha256") == {"a.jsonl": True, "gone.jsonl": False}


def test_measurement_run_does_not_mark_its_own_log_write_as_a_dirty_tree(tmp_path, monkeypatch):
    """측정 기록을 덧붙이는 것 때문에 결과의 git 상태가 '수정됨'이 되면 안 된다(시작 시점의 상태를 쓴다)."""
    d = _copy_data(tmp_path)
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    import adgate.cli as cli

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    out = tmp_path / "artifacts_out.json"
    assert main(["eval", "--datasets", "test", "--systems", "full", "--measure", "--data-dir", str(d), "--out", str(out),
                 "--errors-out", str(tmp_path / "e.json"), "--measurements", str(tmp_path / "M.md")]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["meta"]["dirty"] is False


def test_agreement_reports_quote_overlap_and_exact_matches():
    v = GoldViolation(rule_id="AD-GUA-01", field="headline", quote="무조건 승인")
    examples = [
        ex("a", "REJECT", [v], headline="무조건 승인 대출"),
        ex("b", "REJECT", [v], headline="무조건 승인 대출"),
        ex("c", "REJECT", [v], headline="무조건 승인 대출"),
        ex("d", "APPROVE", headline="좋은 앱"),
    ]

    def lab(quote, field="headline"):
        return {"decision": "REJECT", "violations": [{"rule_id": "AD-GUA-01", "field": field, "quote": quote}], "confidence": 4, "note": ""}

    labels = {
        "a": lab("무조건 승인"),          # 글자까지 같다
        "b": lab("무조건"),               # 겹치지만 짧다
        "c": lab("대출", field="body"),   # 필드가 다르다(겹치지 않음)
        "d": {"decision": "APPROVE", "violations": [], "confidence": 5, "note": ""},
    }
    q = agreement.compare(examples, labels)["quote"]
    assert q == {"gold_quoted": 3, "overlap": 2, "exact": 1}


def test_grounding_counts_a_quote_mismatch_as_a_failure_and_the_gate_rejects_a_vacuous_pass():
    examples = [ex("1", "REJECT", headline="무조건 승인")]
    ok = metrics.compute(examples, [pred("REJECT", finding("AD-GUA-01", "headline", "무조건 승인", "무조건"))])
    assert ok["grounding"]["value"] == 1.0
    p = Prediction("REJECT", [], 1.0, quote_mismatch=1)  # 파이프라인이 어긋난 인용을 지웠다
    bad = metrics.compute(examples, [p])
    assert bad["grounding"]["k"] == 0 and bad["grounding"]["n"] == 1 and bad["grounding"]["value"] == 0.0
    assert not next(c for c in gate.acceptance(bad) if c.id == "H2").ok
    empty = metrics.compute([ex("2", "APPROVE", headline="좋은 앱")], [pred("APPROVE")])
    assert empty["grounding"]["value"] is None and not next(c for c in gate.acceptance(empty) if c.id == "H2").ok


# --------------------------------------------------------------------------- 동결 셋 가드는 열린 채로 실패하지 않는다 (리뷰 H8)
def _eval_args(d, tmp_path, *extra):
    return ["eval", *extra, "--data-dir", str(d), "--out", str(tmp_path / "o.json"), "--errors-out", str(tmp_path / "e.json"),
            "--measurements", str(tmp_path / "M.md")]


def test_dataset_names_are_an_allowlist_so_a_path_cannot_dodge_the_measure_guard(tmp_path):
    d = _copy_data(tmp_path)
    for bad in ("./test", "../data/test", "test.jsonl", "Test"):
        with pytest.raises(SystemExit, match="알 수 없는 데이터셋"):
            main(_eval_args(d, tmp_path, "--datasets", bad))
    assert not (tmp_path / "M.md").exists()


def test_a_missing_or_incomplete_manifest_refuses_frozen_sets(tmp_path):
    d = _copy_data(tmp_path)
    (d / "MANIFEST.sha256").unlink()
    with pytest.raises(SystemExit, match="MANIFEST.sha256"):
        main(_eval_args(d, tmp_path, "--datasets", "test", "--measure"))
    (d / "MANIFEST.sha256").write_text("", encoding="utf-8")  # 비어 있다
    with pytest.raises(SystemExit, match="매니페스트|MANIFEST"):
        main(_eval_args(d, tmp_path, "--datasets", "test", "--measure"))
    with pytest.raises(SystemExit, match="MANIFEST"):
        main(["gate", "--data-dir", str(d)])


def test_measuring_a_frozen_set_always_logs_even_if_the_user_picked_other_systems(tmp_path):
    d = _copy_data(tmp_path)
    assert main(_eval_args(d, tmp_path, "--datasets", "test", "--measure", "--systems", "B1_blocklist")) == 0
    rows = [ln for ln in (tmp_path / "M.md").read_text(encoding="utf-8").splitlines() if ln.startswith("| 20")]
    assert len(rows) == 1 and "| full |" in rows[0]


def test_unknown_git_state_is_logged_as_unknown_not_clean(tmp_path, monkeypatch):
    import adgate.cli as cli

    d = _copy_data(tmp_path)
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    monkeypatch.setattr(cli, "ROOT", plain)  # git 저장소가 아닌 곳
    assert main(_eval_args(d, tmp_path, "--datasets", "test", "--measure", "--systems", "full")) == 0
    row = next(ln for ln in (tmp_path / "M.md").read_text(encoding="utf-8").splitlines() if ln.startswith("| 20"))
    assert "unknown" in row and "알 수 없음" in row and "깨끗" not in row
    assert json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))["meta"]["dirty"] is None
