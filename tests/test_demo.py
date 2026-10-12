"""통합 데모(adgate/demo.py)의 시험. 데모는 측정이 아니라 *이음새 시험*이다: 한 모듈의 출력이 다음 모듈의 입력이 되는 곳의 검사가 살아 있는지 지킨다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from adgate import demo  # noqa: E402
from adgate.campaign.evaluate import oracle_client  # noqa: E402
from adgate.campaign.suite import load_split  # noqa: E402
from adgate.creative.suite import load_all, standin_client  # noqa: E402
from adgate.llm import HybridClient, RecordingStore  # noqa: E402


@pytest.fixture(scope="module")
def offline() -> dict:
    return demo.run_demo("offline")


def test_offline_demo_runs_end_to_end_and_every_seam_check_passes(offline: dict) -> None:
    assert offline["all_ok"] and not offline["errors"], [c for c in offline["checks"] if not c["ok"]]
    assert len(offline["checks"]) >= 9
    assert offline["meta"]["mode"] == "offline" and "측정이 아니다" in offline["meta"]["note"]


def test_setup_reaches_ready_only_through_a_confirmation_and_never_takes_the_injected_value(offline: dict) -> None:
    s = offline["setup"]
    assert s["final_status"] == "ready" and s["turns"][-2]["status"] == "awaiting_confirm"
    assert all(not (a["path"] == "daily_budget_krw" and a["value"] >= 100_000_000) for t in s["turns"] for a in t["accepted"])
    assert s["spec"]["daily_budget_krw"]["value"] == 50_000 and all(v["source"] in ("user", "derived") for v in s["spec"].values())
    assert s["assumed_defaults"] and not set(s["assumed_defaults"]) & set(s["spec"])     # 가정한 값은 사양과 따로 표시된다


def test_creative_step_never_echoes_hidden_text_or_planted_claims(offline: dict) -> None:
    c = offline["creative"]
    assert c["category_matches_conversation"] and c["has_landing_claims"] and c["hidden_dropped"] >= 1
    assert c["status"] in ("ok", "landing_review") and c["recommended"]
    assert all(not x["hidden_text_echoed"] and not x["fabricated_numbers"] and not x["gold_leaks"] for x in c["recommended"])


def test_review_step_fixes_what_can_be_fixed_and_hands_the_rest_to_a_human(offline: dict) -> None:
    first, second = offline["review"]["drafts"]
    assert first["outcome"] == "REJECT" and all(r["quote"] for r in first["reasons"])
    assert first["rewrite_status"] == "verified" and first["outcome_after"] == "APPROVE"            # 광고 문구만 보면 고쳐서 통과
    assert first["with_landing"]["outcome"] == "REVIEW" and first["with_landing"]["landing_findings"]  # 랜딩이 스스로 위반 주장을 하면 사람에게
    assert second["rewrite_status"] == "unavailable" and second["rewritten"] is None and second["unresolved"]   # 질병 표방은 고치지 않는다


def test_report_step_is_verified_and_the_seed_sweep_is_not_cherry_picked(offline: dict) -> None:
    r = offline["report"]
    assert [x["seed"] for x in r["seed_sweep"]] == list(demo.SEEDS) and len(r["seed_sweep"]) == 5
    shown = r["shown"]
    assert shown["caught"] and shown["acted"] and all(e["report_problems"] == [] for e in shown["episodes"])
    assert offline["loop"]["diagnosed_action"] == "fix_rejected_ad" and offline["loop"]["outcome_with_landing"] == "REVIEW"
    # 같은 데이터를 일반 대시보드 규칙(naive)이 보면 다른(틀린) 원인을 가리킨다 — 비교가 전사에 남는다
    assert shown["naive"] and shown["naive"][0]["cause"] != "ad_rejected"


def test_the_demo_is_deterministic(offline: dict) -> None:
    assert demo.run_demo("offline") == offline


def test_recorded_mode_replays_recordings_and_matches_the_offline_run(tmp_path: Path, offline: dict) -> None:
    """같은 단계를 실호출 *대신 대역*으로 녹음한 파일을 recorded 모드로 재생하면 offline 과 같은 전사가 나온다 — 재생 배관이 조용히 다른 것을 쓰지 않는다."""
    dev, test = load_split()
    sc = next(s for s in [*dev, *test] if s.id == demo.DEFAULT_SCENARIO)
    d, t = load_all()
    lp = next(x for x in [*d, *t] if x.product.id == demo.DEFAULT_PRODUCT)
    for suite, live, step in (
        ("campaign", oracle_client(sc), lambda c: demo.step_setup(sc, c, "x")),
        ("creative", standin_client({"lp": lp}), lambda c: demo.step_creative(lp, "supplement", c, "x")),
    ):
        out = tmp_path / "data" / "llm" / suite / "t.jsonl"
        out.parent.mkdir(parents=True)
        step(HybridClient(RecordingStore(out, None), live, "case"))
        assert out.read_text(encoding="utf-8").strip()
    rec = demo.run_demo("recorded", variant="t", root=tmp_path)
    assert not rec["errors"] and rec["all_ok"]
    for key in ("turns", "spec", "final_status"):
        assert rec["setup"][key] == offline["setup"][key]
    assert rec["creative"]["recommended"] == offline["creative"]["recommended"] and "녹음 재생" in rec["setup"]["source"]


def test_recorded_mode_without_a_recording_reports_the_gap_instead_of_substituting(tmp_path: Path) -> None:
    rec = demo.run_demo("recorded", variant="없음", root=tmp_path)
    assert set(rec["errors"]) == {"setup", "creative"} and "녹음 없음" in rec["errors"]["setup"] and not rec["all_ok"]


def test_a_recording_that_lacks_a_call_is_an_error_not_an_invented_answer(tmp_path: Path) -> None:
    p = tmp_path / "data" / "llm" / "campaign"
    p.mkdir(parents=True)
    (p / "t.jsonl").write_text("", encoding="utf-8")                  # 빈 녹음: 첫 호출부터 없다
    (tmp_path / "data" / "llm" / "creative").mkdir(parents=True)
    (tmp_path / "data" / "llm" / "creative" / "t.jsonl").write_text("", encoding="utf-8")
    rec = demo.run_demo("recorded", variant="t", root=tmp_path)
    assert "녹음에 없는 호출" in rec["errors"]["setup"] and "녹음에 없는 호출" in rec["errors"]["creative"] and not rec["all_ok"]


def test_unknown_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="모드"):
        demo.run_demo("live")
    with pytest.raises(ValueError, match="변형"):
        demo.run_demo("recorded")
    with pytest.raises(ValueError, match="시나리오"):
        demo.run_demo("offline", scenario_id="S-ZZ")
    with pytest.raises(ValueError, match="상품"):
        demo.run_demo("offline", product_id="P-ZZ")


def test_transcript_renders_every_section_and_labels_offline_text_as_not_model_written(offline: dict) -> None:
    text = demo.transcript(offline)
    for heading in ("## ① 셋업 대화", "## ② 소재 생성", "## ③ 심사", "## ④ 성과 리포트", "## ⑤ 고리 닫기", "## 이음새 검사"):
        assert heading in text
    assert "모델 아님" in text and "고르지 않았다" in text


def test_dump_writes_json(tmp_path: Path, offline: dict) -> None:
    p = tmp_path / "x" / "demo.json"
    demo.dump(offline, p)
    assert json.loads(p.read_text(encoding="utf-8")) == json.loads(json.dumps(offline, ensure_ascii=False))


def test_committed_offline_artifact_matches_a_fresh_run_so_the_doc_cannot_drift() -> None:
    """docs/DEMO.md 는 artifacts/demo_offline.json 에서 만든다. 코드가 바뀌어 전사가 달라지면 이 시험이 산출물을 다시 만들라고 알려 준다(`adgate demo --out artifacts/demo_offline.json`)."""
    root = Path(__file__).resolve().parents[1]
    committed = json.loads((root / "artifacts" / "demo_offline.json").read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(demo.run_demo("offline"), ensure_ascii=False))
    assert fresh == committed


def test_committed_recorded_artifacts_are_complete_runs_of_real_recordings() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in sorted((root / "artifacts").glob("demo_recorded_*.json")):
        res = json.loads(path.read_text(encoding="utf-8"))
        assert res["meta"]["mode"] == "recorded" and res["meta"]["variant"] and not res["errors"] and res["all_ok"], path.name
        assert "녹음 재생" in res["setup"]["source"] and "녹음 재생" in res["creative"]["source"]
