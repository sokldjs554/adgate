from __future__ import annotations

import hashlib
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


# --------------------------------------------------------------------------- LLM judge 녹음 재생(docs/JUDGE_REPLAY.md)
REPLAY = ROOT / "docs" / "JUDGE_REPLAY.md"
# 사전등록 커밋(1c22cc1)의 1–3절 본문 해시. 결과를 본 뒤에 사전등록을 고치지 못하게 한다.
PREREGISTERED_SHA256 = "2113298c0c8c7bd07c97fb7eebf1490e23addcb60c66cdf39eeb228919cb9157"


def _judge() -> dict:
    return json.loads((ROOT / "artifacts" / "results_judge.json").read_text(encoding="utf-8"))


def _replay_ids(name: str) -> set[str]:
    m = re.search(rf"<!-- ids:{name} -->(.*?)<!-- /ids -->", REPLAY.read_text(encoding="utf-8"), re.DOTALL)
    assert m, f"JUDGE_REPLAY.md 에 ids:{name} 표식이 없다"
    return set(re.findall(r"[DTR]-\d{3}", m.group(1)))


def test_judge_replay_preregistration_text_is_unchanged():
    text = REPLAY.read_text(encoding="utf-8")
    body = text[text.index("## 1. 무엇을 알고 싶은가"):text.index("\n## 4. 결과")]
    assert hashlib.sha256(body.strip("\n").encode("utf-8")).hexdigest() == PREREGISTERED_SHA256, "사전등록(1–3절)을 측정 뒤에 고쳤다"


def test_judge_replay_doc_names_exactly_the_creatives_the_artifact_says():
    summ = _judge()["meta"]["judge_replay"]["datasets"]
    # judge 가 고친 위험 승인 = F-14 가 '어휘로는 못 막는다'고 남겨 둔 패러프레이즈 14건과 정확히 같다
    assert _replay_ids("judge_fixed") == set(summ["redteam"]["unsafe_fixed"]) == _doc_ids("paraphrase_v0")
    assert not summ["dev"]["unsafe_fixed"] and not summ["test"]["unsafe_fixed"]
    assert _replay_ids("judge_to_reject") == {i for d in summ.values() for i in d["approve_gold_to_reject"]}


def test_judge_replay_artifact_was_measured_on_the_committed_recording():
    meta = _judge()["meta"]["judge_replay"]
    rec = ROOT / "data" / "judge_replay" / "recording.jsonl"
    assert hashlib.sha256(rec.read_bytes()).hexdigest() == meta["file_sha256"]
    assert meta["responses"] == 209 and meta["misses"] == 0
    assert all(d["deterministic_lost"] == 0 for d in meta["datasets"].values())


def test_judge_recording_is_not_stale_the_current_detector_sends_the_same_prompts(tmp_path):
    """녹음은 대역이 본 프롬프트(탐지기가 이미 찾은 것 포함)에 대한 응답이다. 탐지기가 바뀌어 프롬프트가 달라지면 녹음은 낡는다."""
    out = tmp_path / "prompts"
    assert main(["judge-export", "--datasets", "dev,test,redteam", "--data-dir", str(ROOT / "data"), "--out-dir", str(out), "--shards", "9"]) == 0
    expected = dict(reversed(line.split()) for line in (ROOT / "data/judge_replay/PROMPTS.sha256").read_text(encoding="utf-8").splitlines() if line.strip())
    assert len(expected) == 11
    for name, digest in expected.items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest, f"{name} 이(가) 대역이 본 것과 다르다: 다시 녹음하고 다시 재거나 낡았다고 적는다"


def test_judge_confidence_claim_in_the_doc_is_recomputed_from_the_recording():
    from adgate.cli import load_dataset
    from adgate.judge_recorded import creative_key

    summ = _judge()["meta"]["judge_replay"]["datasets"]
    recording = {}
    for ln in (ROOT / "data/judge_replay/recording.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(ln)
        recording[row["key"]] = row["response"]
    changed = {(ds, x["id"]): x for ds, d in summ.items() for x in d["changed"]}
    wrong: list[float] = []
    helped: list[float] = []
    for ds in summ:
        for ex in load_dataset(ds, ROOT / "data"):
            x = changed.get((ds, ex.id))
            if x is None:
                continue
            for f in recording[creative_key(ex.creative)]["findings"]:
                (wrong if x["gold"] == "APPROVE" else helped).append(f["confidence"])
    assert (len(wrong), min(wrong), max(wrong)) == (4, 0.6, 0.7)
    assert (len(helped), min(helped), max(helped), sum(c > 0.7 for c in helped)) == (18, 0.65, 0.92, 15)


def test_judge_replay_section_renders_from_the_committed_artifact():
    text = "\n".join(docs.judge_replay_md(_judge(), "## 9. LLM judge — 녹음 재생 (실호출 아님)"))
    assert text.startswith("## 9.") and "운영 모델의 성능이 아니다" in text and "자동 처리 정밀도" in text
    assert text.count("전체 + LLM judge (녹음, 정책 등급 그대로)") == 3 and text.count("전체 + LLM judge (녹음, REVIEW 까지만)") == 3
    assert "dev/D-004" in text and "dev/D-060" in text and "test/T-007" in text


LIVE = ROOT / "docs" / "JUDGE_LIVE.md"
# 실호출 측정 사전등록(1–4절)의 본문 해시. 실호출 *전에* 커밋했다. 결과(5절 이후)는 뒤에 덧붙인다.
LIVE_PREREGISTERED_SHA256 = "dc5e1a2d9610331b80ce386df418ad8d137db46d732f6316fbf7e91c82c700bd"


def test_judge_live_preregistration_text_is_unchanged():
    text = LIVE.read_text(encoding="utf-8")
    end = text.index("\n## 5.") if "\n## 5." in text else len(text)
    body = text[text.index("## 1. 무엇을 알고 싶은가"):end]
    assert hashlib.sha256(body.strip("\n").encode("utf-8")).hexdigest() == LIVE_PREREGISTERED_SHA256, "실호출 사전등록(1–4절)을 고쳤다"


def test_judge_live_section_renders_calls_and_the_comparison_with_the_stand_ins(tmp_path):
    """실호출 산출물이 생기면 결과 문서에 호출 요약·지연·대역과의 견줌이 나온다(합성 입력으로 렌더링만 시험한다)."""
    root = tmp_path
    (root / "artifacts").mkdir()
    (root / "data/judge_live").mkdir(parents=True)
    (root / "artifacts/results_judge_live.json").write_text(json.dumps(_judge()), encoding="utf-8")
    (root / "data/judge_live/summary.json").write_text(json.dumps({
        "model": "claude-opus-5-5", "effort": "medium", "calls": 4, "ok": 3, "failed": 1, "input_tokens": 1000, "output_tokens": 2000,
        "cache_write_tokens": 2621, "cache_read_tokens": 7863, "estimated_usd": 0.1234, "max_usd": 6.0,
    }), encoding="utf-8")
    calls = [{"key": "a", "ok": True, "latency_ms": 4000}, {"key": "b", "ok": True, "latency_ms": 6000},
             {"key": "c", "ok": True, "latency_ms": 8000}, {"key": "d", "ok": False, "error": "APITimeoutError", "latency_ms": 60000}]
    (root / "data/judge_live/calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls), encoding="utf-8")
    (root / "artifacts/judge_compare.json").write_text(json.dumps({
        "n": 209, "compared": 207, "agree_empty": 170, "agree_nonempty": 20, "rule_sets_equal": 18, "only_a": 8, "only_b": 9, "error_b": 2,
        "agreement": {"k": 190, "n": 207, "value": 0.918, "ci": [0.87, 0.95]},
    }), encoding="utf-8")
    src = docs.gather(root)
    info = src["judge_live_info"]
    assert info["calls"] == 4 and info["errors"] == {"APITimeoutError": 1} and info["latency_s"]["max"] == 60.0 and info["latency_s"]["p50"] == 8.0
    text = "\n".join(docs.judge_replay_md(src["judge_live"], "## 8. LLM judge — 실호출 녹음", live=info, compare=src["judge_compare"]))
    assert "실제 API 호출" in text and "claude-opus-5-5" in text and "운영 모델의 성능이 아니다" not in text
    assert "### 호출" in text and "APITimeoutError 1" in text and "$0.12 ($6.00)" in text and "2,621 · 7,863" in text
    assert "190/207" in text and "실호출 실패 2" in text
    tree = docs.lookup_sources(src)
    assert tree["judge_live"]["info"]["calls"] == 4 and tree["judge_live"]["compare"]["compared"] == 207


# --------------------------------------------------------------------------- LLM judge 실호출(docs/JUDGE_LIVE.md 6절)
LIVE_DATA = ROOT / "data" / "judge_live"


def _live() -> dict:
    return json.loads((ROOT / "artifacts" / "results_judge_live.json").read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _live_ids(name: str) -> set[str]:
    m = re.search(rf"<!-- ids:{name} -->(.*?)<!-- /ids -->", LIVE.read_text(encoding="utf-8"), re.DOTALL)
    assert m, f"JUDGE_LIVE.md 에 ids:{name} 표식이 없다"
    return set(re.findall(r"[DTR]-\d{3}", m.group(1)))


def test_judge_live_doc_names_exactly_the_creatives_the_artifact_says():
    summ = _live()["meta"]["judge_replay"]["datasets"]
    assert _live_ids("live_fixed") == set(summ["redteam"]["unsafe_fixed"]) == _doc_ids("paraphrase_v0")  # 대역이 고친 14건과 같다
    assert _live_ids("live_to_reject") == {i for d in summ.values() for i in d["approve_gold_to_reject"]}
    assert all(d["deterministic_lost"] == 0 for d in summ.values())


def test_judge_live_artifact_was_measured_on_the_committed_recording_with_a_clean_tree():
    art = _live()
    meta = art["meta"]["judge_replay"]
    assert hashlib.sha256((LIVE_DATA / "recording.jsonl").read_bytes()).hexdigest() == meta["file_sha256"]
    assert (meta["responses"], meta["misses"], meta["errors"]) == (209, 0, 0)
    assert art["meta"]["dirty"] is False  # 사전등록: 녹음을 커밋한 깨끗한 작업트리에서 한 번 집계한다


def test_judge_live_call_log_agrees_with_the_summary_and_the_recording():
    summary = json.loads((LIVE_DATA / "summary.json").read_text(encoding="utf-8"))
    calls, rows = _jsonl(LIVE_DATA / "calls.jsonl"), _jsonl(LIVE_DATA / "recording.jsonl")
    assert len(calls) == len(rows) == summary["calls"] == summary["ok"] == 209 and summary["failed"] == summary["refusals"] == 0
    assert {c["key"] for c in calls} == {r["key"] for r in rows} and all(c["ok"] and c["stop_reason"] == "end_turn" for c in calls)
    assert summary["estimated_usd"] < summary["max_usd"] == 6.0 and summary["model"] == "claude-opus-5-5" and summary["effort"] == "medium"
    assert sum(c["cache_read_tokens"] for c in calls) == summary["cache_read_tokens"] and sum(c["input_tokens"] for c in calls) == summary["input_tokens"]


def test_judge_live_comparison_artifact_is_derived_from_the_two_committed_recordings():
    from adgate.eval.judge_replay import compare_recordings

    a = {r["key"]: r for r in _jsonl(ROOT / "data/judge_replay/recording.jsonl")}
    b = {r["key"]: r for r in _jsonl(LIVE_DATA / "recording.jsonl")}
    assert compare_recordings(a, b) == json.loads((ROOT / "artifacts/judge_compare.json").read_text(encoding="utf-8"))


def test_judge_live_prose_about_the_26_live_only_items_and_the_confidences_is_recomputed():
    from adgate.cli import load_dataset
    from adgate.judge_recorded import creative_key

    summ = _live()["meta"]["judge_replay"]["datasets"]
    changed = {(ds, x["id"]): x for ds, d in summ.items() for x in d["changed"]}
    stand_in = {r["key"]: r["response"]["findings"] for r in _jsonl(ROOT / "data/judge_replay/recording.jsonl")}
    live = {r["key"]: r["response"]["findings"] for r in _jsonl(LIVE_DATA / "recording.jsonl")}
    live_only, wrong, helped = [], [], []
    top: dict[str, float] = {}
    for ds in summ:
        for ex in load_dataset(ds, ROOT / "data"):
            k = creative_key(ex.creative)
            if live[k] and not stand_in[k]:
                live_only.append((ds, ex.id))
            x = changed.get((ds, ex.id))
            if x is not None:
                top[f"{ds}/{ex.id}"] = max(f["confidence"] for f in live[k])
                (wrong if x["gold"] == "APPROVE" else helped).extend(f["confidence"] for f in live[k])
    assert len(live_only) == 26 and {i for i in live_only if i in changed} == {("dev", "D-011"), ("dev", "D-059"), ("redteam", "R-010")}  # 나머지 23건은 판정 불변
    assert (len(wrong), min(wrong), max(wrong)) == (6, 0.45, 0.6)                                   # 정상 소재를 건드린 5소재의 후보
    helped_top = sorted(v for key, v in top.items() if changed[tuple(key.split("/"))]["gold"] != "APPROVE")
    assert len(helped_top) == 17 and helped_top[:2] == [0.6, 0.7] and min(helped_top[2:]) >= 0.75   # 위험을 잡은 17소재
    assert max(v for key, v in top.items() if changed[tuple(key.split("/"))]["gold"] == "APPROVE") == 0.6
