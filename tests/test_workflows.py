"""워크플로 파일의 구조 검사. 비용이 드는 워크플로가 실수로 다른 트리거로 돌거나 키를 흘리지 않게 한다."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def load(name: str) -> dict:
    data = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 에서 `on:` 키가 True 로 읽힌다
    return data


def test_every_workflow_parses_and_has_jobs_with_steps():
    assert {w.name for w in WORKFLOWS} >= {"ci.yml", "judge-live.yml"}
    for w in WORKFLOWS:
        data = load(w.name)
        assert data["jobs"], w.name
        for job in data["jobs"].values():
            assert job["steps"], w.name


def test_ci_never_touches_the_api_key():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ANTHROPIC" not in text and "secrets." not in text


def test_the_paid_workflow_runs_only_by_hand_and_only_the_keyless_job_can_write():
    wf = load("judge-live.yml")
    assert set(wf["on"]) == {"workflow_dispatch"}                      # push·pull_request·schedule 로는 돌지 않는다
    assert wf["permissions"] == {"contents": "read"}                   # 기본은 읽기뿐
    text = (ROOT / ".github" / "workflows" / "judge-live.yml").read_text(encoding="utf-8")
    assert text.count("secrets.ANTHROPIC_API_KEY") == 2                # 호출하는 두 단계에서만
    paid = 0
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            run = step.get("run", "")
            assert "${{" not in run, f"입력·비밀값을 셸 명령에 직접 끼우지 않는다(환경 변수로): {run}"
            assert "echo" not in run and "ANTHROPIC_API_KEY" not in run  # 키를 출력하지 않는다
            if "secrets.ANTHROPIC_API_KEY" in str(step.get("env", {})):
                paid += 1
                assert name == "judge" and step["if"] in ("inputs.mode == 'check' || inputs.mode == 'record'", "inputs.mode == 'record'")
    assert paid == 2
    # 쓰기 권한이 있는 작업에는 키가 없고, 키가 있는 작업에는 쓰기 권한이 없다(최소 권한)
    assert "permissions" not in wf["jobs"]["judge"]
    assert wf["jobs"]["publish"]["permissions"] == {"contents": "write"} and "secrets." not in str(wf["jobs"]["publish"]["steps"]).replace("github.token", "")
    assert wf["jobs"]["publish"]["needs"] == "judge"


def test_llm_live_is_manual_only_keyed_steps_are_the_two_calling_ones_and_publish_has_no_key():
    wf = load("llm-live.yml")
    assert set(wf["on"]) == {"workflow_dispatch"}
    assert wf["permissions"] == {"contents": "read"}
    text = (ROOT / ".github" / "workflows" / "llm-live.yml").read_text(encoding="utf-8")
    assert text.count("secrets.ANTHROPIC_API_KEY") == 2
    keyed = []
    for name, job in wf["jobs"].items():
        for step in job["steps"]:
            run = step.get("run", "")
            assert "${{" not in run, f"입력·비밀값을 셸 명령에 직접 끼우지 않는다(환경 변수로): {run}"
            assert "echo $" not in run and "ANTHROPIC_API_KEY" not in run
            if "secrets.ANTHROPIC_API_KEY" in str(step.get("env", {})):
                keyed.append((name, step["name"]))
    assert [n for n, _ in keyed] == ["llm", "llm"]
    assert "permissions" not in wf["jobs"]["llm"]
    assert wf["jobs"]["publish"]["permissions"] == {"contents": "write"} and "secrets." not in str(wf["jobs"]["publish"]["steps"]).replace("github.token", "")
    assert wf["jobs"]["publish"]["needs"] == "llm"
    # 이름 입력은 셸에 닿기 전에 정규식으로 거른다
    assert any("^[a-z0-9_]+$" in s.get("run", "") for s in wf["jobs"]["llm"]["steps"])


def test_every_suite_the_workflow_can_run_is_registered_and_dry_runs_without_a_key():
    from adgate import suites

    assert {"campaign", "campaign_reply"} <= set(suites.names())
    for n in suites.names():
        s = suites.get(n)
        assert s.variants and s.prereg.startswith("docs/EVAL_")
        assert (ROOT / s.prereg).exists()
