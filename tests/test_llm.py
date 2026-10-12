"""공통 LLM 계층: 열쇠, 요청 형태, 응답 처리, 녹음·재생·이어하기·예산 상한. 실제 API 는 부르지 않는다."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from adgate.llm import (
    HybridClient,
    Image,
    LLMAccountError,
    LLMBudgetExceeded,
    LLMMissing,
    LLMProtocolError,
    LLMRecordedFailure,
    LLMRefused,
    LLMTask,
    LLMTruncated,
    RecordedClient,
    RecordingStore,
    ScriptedClient,
    Spend,
    is_account_error,
    load_rows,
    record_cases,
    replay_cases,
    task_key,
)
from adgate.llm.anthropic_client import FALLBACK_BETA, AnthropicClient, parse_message

TASK = LLMTask(name="t.echo", version="v1", system="시스템 프롬프트", schema={"type": "object", "properties": {"x": {"type": "integer"}}})
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


def msg(text: str | None, *, stop: str = "end_turn", usage: Any = None) -> Any:
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    usage = usage or SimpleNamespace(input_tokens=100, output_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(stop_reason=stop, content=content, usage=usage, stop_details=SimpleNamespace(category="cyber"))


class FakeAPI:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


class FakeSDK:
    def __init__(self, *responses: Any) -> None:
        self.messages = FakeAPI(*responses)
        self.beta = SimpleNamespace(messages=FakeAPI(*responses))


# --------------------------------------------------------------------------- 열쇠
def test_key_changes_with_every_prompt_ingredient_and_ignores_the_model():
    base = task_key(TASK, "입력")
    assert base == task_key(TASK, ["입력"])                                         # 문자열과 조각 하나는 같다
    assert base != task_key(TASK, "입력2")
    assert base != task_key(LLMTask("t.echo", "v2", TASK.system, TASK.schema), "입력")        # 프롬프트 버전
    assert base != task_key(LLMTask("t.other", "v1", TASK.system, TASK.schema), "입력")       # 과제 이름
    assert base != task_key(LLMTask("t.echo", "v1", "다른 시스템", TASK.schema), "입력")       # 시스템 프롬프트
    assert base != task_key(LLMTask("t.echo", "v1", TASK.system, {"type": "object"}), "입력")  # 스키마
    # effort·max_tokens 는 열쇠에 넣지 않는다(녹음 하나는 한 번의 측정이고 모델·effort 는 호출 기록에 남는다)
    assert base == task_key(LLMTask("t.echo", "v1", TASK.system, TASK.schema, max_tokens=99, effort="low"), "입력")


def test_image_key_uses_file_content_not_path(tmp_path):
    (tmp_path / "a.png").write_bytes(PNG)
    (tmp_path / "b.png").write_bytes(PNG)
    (tmp_path / "c.png").write_bytes(PNG + b"x")
    ka = task_key(TASK, ["설명", Image("a.png")], tmp_path)
    assert ka == task_key(TASK, ["설명", Image("b.png")], tmp_path)                 # 경로가 달라도 내용이 같으면 같다
    assert ka != task_key(TASK, ["설명", Image("c.png")], tmp_path)                 # 한 바이트만 달라도 다르다
    assert ka != task_key(TASK, ["설명", Image("a.png", "image/jpeg")], tmp_path)   # 형식도 열쇠에 든다


# --------------------------------------------------------------------------- 요청 형태
def test_request_is_structured_output_with_cached_system_and_no_forbidden_params(tmp_path):
    (tmp_path / "a.png").write_bytes(PNG)
    c = AnthropicClient(client=object(), base_dir=tmp_path)
    kw = c.request(TASK, ["본문", Image("a.png")])
    assert kw["model"] == "claude-sonnet-5-5" and kw["max_tokens"] == TASK.max_tokens
    assert kw["system"] == [{"type": "text", "text": TASK.system, "cache_control": {"type": "ephemeral"}}]
    text_block, image_block = kw["messages"][0]["content"]
    assert text_block == {"type": "text", "text": "본문"}
    assert image_block["type"] == "image" and image_block["source"]["type"] == "base64" and image_block["source"]["media_type"] == "image/png"
    assert kw["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": TASK.schema}}
    assert not {"tool_choice", "tools", "thinking", "temperature", "top_p", "betas", "fallbacks"} & set(kw)
    fb = AnthropicClient(client=object(), fallbacks=True).request(TASK, "x")
    assert fb["betas"] == [FALLBACK_BETA] and fb["fallbacks"] == "default"


# --------------------------------------------------------------------------- 응답
def test_call_returns_parsed_object_and_meta_and_settles_spend():
    sdk = FakeSDK(msg('{"x": 3}'))
    c = AnthropicClient(client=sdk, model="claude-haiku-5-5", max_usd=1.0)
    r = c.call(TASK, "입력")
    assert r.response == {"x": 3} and r.meta["input_tokens"] == 100 and r.meta["output_tokens"] == 20 and r.meta["model"] == "claude-haiku-5-5"
    assert c.spend.calls == 1 and 0 < c.spend.usd < 0.01 and c.last_meta["stop_reason"] == "end_turn"
    assert len(sdk.messages.requests) == 1 and not sdk.beta.messages.requests      # 폴백을 끄면 일반 경로


@pytest.mark.parametrize(
    ("message", "error"),
    [
        (msg(None, stop="refusal"), LLMRefused),
        (msg('{"x":', stop="max_tokens"), LLMTruncated),
        (msg(None), LLMProtocolError),                       # 텍스트 블록 없음
        (msg("그냥 글"), LLMProtocolError),                   # JSON 아님
        (msg("[1, 2]"), LLMProtocolError),                   # 객체 아님
    ],
)
def test_every_abnormal_response_becomes_an_llm_error(message, error):
    with pytest.raises(error):
        parse_message(message)
    c = AnthropicClient(client=FakeSDK(message))
    with pytest.raises(error):
        c.call(TASK, "입력")
    assert c.spend.failures + c.spend.refusals == 1


def test_a_transport_failure_is_charged_conservatively_and_reraised():
    c = AnthropicClient(client=FakeSDK(TimeoutError("t")), max_usd=1.0)
    with pytest.raises(TimeoutError):
        c.call(TASK, "입력")
    assert c.spend.failures == 1 and c.spend.failed_usd > 0 and c.spend.reserved == 0


def test_budget_stops_before_the_call_is_made():
    sdk = FakeSDK(msg('{"x": 1}'))
    c = AnthropicClient(client=sdk, max_usd=0.0000001)
    with pytest.raises(LLMBudgetExceeded):
        c.call(TASK, "입력")
    assert not sdk.messages.requests
    assert isinstance(Spend("claude-sonnet-5-5", 1.0, exc=LLMBudgetExceeded), Spend)


# --------------------------------------------------------------------------- 재생
def test_recorded_client_hit_miss_and_recorded_failure():
    k = task_key(TASK, "가")
    rc = RecordedClient({k: {"key": k, "response": {"x": 1}}, task_key(TASK, "나"): {"key": "n", "error": "APITimeoutError"}})
    assert rc.call(TASK, "가").response == {"x": 1}
    with pytest.raises(LLMRecordedFailure, match="APITimeoutError"):
        rc.call(TASK, "나")
    with pytest.raises(LLMMissing):
        rc.call(TASK, "다")
    assert rc.calls == 3 and rc.misses == 1 and rc.has(TASK, "가") and not rc.has(TASK, "다")


def test_load_rows_rejects_duplicate_keys(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"key": "a", "response": {}}\n{"key": "a", "response": {}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="두 번"):
        load_rows(p)
    assert load_rows(tmp_path / "없음.jsonl") == {}


# --------------------------------------------------------------------------- 녹음
def loop_driver(payload: Any, client: Any) -> dict[str, Any]:
    """닫힌 고리: 두 번째 입력이 첫 응답에 달려 있다. 응답이 같으면 고리 전체가 그대로 재현돼야 한다."""
    first = client.call(TASK, f"1:{payload}").response["x"]
    second = client.call(TASK, f"2:{payload}:{first}").response["x"]
    return {"first": first, "second": second}


def echo(task: LLMTask, parts: list[Any]) -> dict[str, Any]:
    text = parts[0]
    if "boom" in text:
        raise TimeoutError("시간 초과")
    return {"x": len(text)}


def cases(*ids: str) -> list[tuple[str, Any]]:
    return [(i, i) for i in ids]


def test_record_then_replay_reproduces_the_closed_loop(tmp_path):
    live = ScriptedClient(echo)
    out, log = tmp_path / "rec.jsonl", tmp_path / "calls.jsonl"
    cs = cases("가나", "다라마", "바")
    s = record_cases(cs, loop_driver, live=live, out=out, log=log, workers=3, say=lambda *_: None)
    assert (s["completed"], s["case_errors"], s["live_calls"], s["replayed_calls"]) == (3, 0, 6, 0)
    rows = load_rows(out)
    assert len(rows) == 6
    # 파일은 (사례 순서, 호출 순서)로 정렬돼 있다 — 동시 호출의 완료 순서가 새지 않는다
    lines = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines()]
    assert [(r["case"], r["seq"]) for r in lines] == [("가나", 1), ("가나", 2), ("다라마", 1), ("다라마", 2), ("바", 1), ("바", 2)]
    outcomes = replay_cases(cs, loop_driver, rows)
    assert [o.result for o in outcomes] == [{"first": 4, "second": 6}, {"first": 5, "second": 7}, {"first": 3, "second": 5}]
    assert all(o.error is None and o.misses == 0 for o in outcomes)
    assert len(live.calls) == 6                                        # 재생은 실제 호출을 하지 않는다
    entries = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 6 and all(e["ok"] for e in entries)


def test_rerun_resumes_without_calling_again_and_a_partial_recording_is_completed(tmp_path):
    out, log = tmp_path / "rec.jsonl", tmp_path / "calls.jsonl"
    cs = cases("가나", "다라마")
    record_cases(cs[:1], loop_driver, live=ScriptedClient(echo), out=out, log=log, workers=1, say=lambda *_: None)
    again = ScriptedClient(echo)
    s = record_cases(cs, loop_driver, live=again, out=out, log=log, workers=1, say=lambda *_: None)
    assert len(again.calls) == 2 and s["live_calls"] == 2 and s["replayed_calls"] == 2    # 첫 사례는 재생, 둘째만 실제 호출
    full = ScriptedClient(echo)
    s2 = record_cases(cs, loop_driver, live=full, out=out, log=log, workers=1, say=lambda *_: None)
    assert not full.calls and s2["live_calls"] == 0 and s2["replayed_calls"] == 4


def test_failures_are_recorded_and_replay_fails_in_the_same_place(tmp_path):
    out, log = tmp_path / "rec.jsonl", tmp_path / "calls.jsonl"

    def driver(payload: Any, client: Any) -> str:
        try:
            return str(client.call(TASK, payload).response["x"])
        except LLMRecordedFailure as exc:                              # live 와 replay 가 같은 모양으로 본다
            return f"fail:{exc}"

    cs = cases("정상", "boom")
    s = record_cases(cs, driver, live=ScriptedClient(echo), out=out, log=log, workers=1, say=lambda *_: None)
    assert s["completed"] == 2 and s["live_calls"] == 2
    rows = load_rows(out)
    assert [r.get("error") for r in rows.values()] == [None, "TimeoutError"]
    assert [o.result for o in replay_cases(cs, driver, rows)] == ["2", "fail:TimeoutError"]


def test_budget_stop_preserves_the_recording_and_skips_the_rest(tmp_path):
    out, log = tmp_path / "rec.jsonl", tmp_path / "calls.jsonl"
    state = {"n": 0}

    def handler(task: LLMTask, parts: list[Any]) -> dict[str, Any]:
        state["n"] += 1
        if state["n"] > 2:
            raise LLMBudgetExceeded("상한")
        return {"x": 1}

    cs = cases("a", "b", "c", "d")
    s = record_cases(cs, lambda p, c: c.call(TASK, p), live=ScriptedClient(handler), out=out, log=log, workers=1, say=lambda *_: None)
    assert s["completed"] == 2 and s["skipped_budget"] == 2
    assert len(load_rows(out)) == 2                                    # 상한에 닿기 전의 녹음은 남는다
    # 상한을 올려 다시 돌리면 남은 것만 부른다
    live = ScriptedClient(lambda t, p: {"x": 9})
    s2 = record_cases(cs, lambda p, c: c.call(TASK, p), live=live, out=out, log=log, workers=1, say=lambda *_: None)
    assert len(live.calls) == 2 and s2["completed"] == 4 and len(load_rows(out)) == 4


def test_retry_technical_only_redoes_transient_failures(tmp_path):
    out, log = tmp_path / "rec.jsonl", tmp_path / "calls.jsonl"

    class APITimeoutError(Exception):
        pass

    class Refused(Exception):
        pass

    def first(task: LLMTask, parts: list[Any]) -> dict[str, Any]:
        if parts[0] == "t":
            raise APITimeoutError("t")
        if parts[0] == "r":
            raise Refused("r")
        return {"x": 1}

    def driver(payload: Any, client: Any) -> None:
        try:
            client.call(TASK, payload)
        except LLMRecordedFailure:
            pass

    cs = cases("ok", "t", "r")
    record_cases(cs, driver, live=ScriptedClient(first), out=out, log=log, workers=1, say=lambda *_: None)
    live = ScriptedClient(lambda t, p: {"x": 2})
    s = record_cases(cs, driver, live=live, out=out, log=log, workers=1, say=lambda *_: None, retry_technical=True)
    assert s["retried"] == 1 and [c[1][0] for c in live.calls] == ["t"]                  # 시간 초과 하나만 다시 불렀다
    errors = {r.get("error") for r in load_rows(out).values()}
    assert errors == {None, "Refused"}                                                   # 거절은 모델의 응답이라 그대로 남았다


def test_a_case_that_raises_is_logged_and_does_not_stop_the_others(tmp_path):
    out, log = tmp_path / "rec.jsonl", tmp_path / "calls.jsonl"

    def driver(payload: Any, client: Any) -> None:
        if payload == "버그":
            raise RuntimeError("driver 버그")
        client.call(TASK, payload)

    s = record_cases(cases("버그", "정상"), driver, live=ScriptedClient(echo), out=out, log=log, workers=1, say=lambda *_: None)
    assert (s["completed"], s["case_errors"]) == (1, 1)
    entries = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("case_error") == "RuntimeError" for e in entries)


def test_replay_reports_misses_per_case_instead_of_inventing_answers(tmp_path):
    outcomes = replay_cases(cases("a"), lambda p, c: c.call(TASK, p), {})
    assert outcomes[0].error == "LLMMissing" and outcomes[0].misses == 1 and outcomes[0].result is None


def test_hybrid_client_without_live_is_replay_only():
    store = RecordingStore(None, None, {})
    with pytest.raises(LLMMissing):
        HybridClient(store, None, "c").call(TASK, "x")


# --------------------------------------------------------------------------- 계정 오류(크레딧 소진·인증·권한)
class BadRequestError(Exception):
    """SDK 의 400 을 흉내 낸다(이름만 같다)."""

    status_code = 400


class StatusError(Exception):
    def __init__(self, status: int, text: str = "x") -> None:
        super().__init__(text)
        self.status_code = status


CREDIT = BadRequestError("Error code: 400 - {'type': 'error', 'error': {'message': 'Your credit balance is too low to access the Anthropic API.'}}")


@pytest.mark.parametrize(("exc", "expected"), [
    (CREDIT, True),                                           # 실호출 때 실제로 본 메시지(소재 생성 sonnet 녹음에서 51건)
    (StatusError(401, "invalid x-api-key"), True), (StatusError(402), True), (StatusError(403, "permission"), True),
    (BadRequestError("Error code: 400 - schema is invalid"), False),     # 요청이 잘못된 400 은 계정 문제가 아니다(모델·프롬프트의 실패로 센다)
    (StatusError(429, "rate limited"), False), (StatusError(500), False), (TimeoutError("t"), False),
])
def test_account_errors_are_told_apart_from_request_and_transient_failures(exc, expected):
    assert is_account_error(exc) is expected


def test_the_client_turns_an_account_error_into_a_stop_not_a_model_failure():
    c = AnthropicClient(client=FakeSDK(CREDIT), max_usd=1.0)
    with pytest.raises(LLMAccountError) as info:
        c.call(TASK, "입력")
    assert isinstance(info.value, LLMBudgetExceeded)           # 예산 초과와 같은 방식으로 실행을 멈춘다
    assert c.spend.failures == 1 and c.spend.reserved == 0


def test_an_account_error_is_not_recorded_as_a_failure_and_the_run_resumes_after_it_is_fixed(tmp_path):
    """크레딧이 바닥나면 실행이 멈추고 그 호출은 녹음되지 않는다 — 실패 행이 남으면 재생이 '모델이 실패했다'고 센다(실제로 51건을 그렇게 녹음할 뻔했다)."""
    out, log = tmp_path / "rec.jsonl", tmp_path / "calls.jsonl"
    state = {"n": 0, "broke": True}

    def handler(task: LLMTask, parts: list[Any]) -> dict[str, Any]:
        state["n"] += 1
        if state["broke"] and state["n"] > 2:
            raise LLMAccountError("BadRequestError: credit balance is too low")
        return {"x": state["n"]}

    cs = cases("a", "b", "c", "d")
    said: list[str] = []
    s = record_cases(cs, lambda p, c: c.call(TASK, p), live=ScriptedClient(handler), out=out, log=log, workers=1, say=said.append)
    assert s["completed"] == 2 and s["skipped_budget"] == 2 and any("계정 문제로" in m for m in said)
    rows = load_rows(out)
    assert len(rows) == 2 and all("error" not in r for r in rows.values())        # 실패 행이 없다
    assert not any(not json.loads(ln)["ok"] for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip())
    state["broke"] = False                                                         # 충전했다
    live = ScriptedClient(handler)
    s2 = record_cases(cs, lambda p, c: c.call(TASK, p), live=live, out=out, log=log, workers=1, say=lambda *_: None)
    assert len(live.calls) == 2 and s2["completed"] == 4 and len(load_rows(out)) == 4      # 남은 둘만 부른다


def test_replay_meta_lists_failure_rows_so_environment_errors_are_seen_before_aggregating(tmp_path):
    """녹음에 실패 행이 있으면 재생 결과의 meta 에 이름별 건수가 남고 CLI 가 경고한다(F-28: 크레딧 소진 51건을 모델 실패로 셀 뻔했다)."""
    from adgate import suites

    def driver(payload: Any, client: Any) -> Any:
        try:
            return client.call(TASK, payload).response
        except LLMRecordedFailure:
            return None

    cs = cases("정상", "실패")
    suite = suites.Suite("toy", "장난감", "docs/x.md", {"v": suites.Variant("v", "claude-haiku-5-5")}, lambda v: cs, lambda v: driver, lambda p: ScriptedClient(echo),
                         lambda v, outcomes: {"ok": [o.case for o in outcomes if o.result is not None]})
    rec = tmp_path / "data" / "llm" / "toy"
    rec.mkdir(parents=True)
    rows = [{"key": task_key(TASK, "정상"), "task": "t.echo", "case": "정상", "seq": 1, "response": {"x": 1}},
            {"key": task_key(TASK, "실패"), "task": "t.echo", "case": "실패", "seq": 1, "error": "BadRequestError"}]
    (rec / "v.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    res = suites.replay(suite, "v", root=tmp_path)
    assert res["meta"]["recorded_failures"] == {"BadRequestError": 1} and res["meta"]["recorded_rows"] == 2
    assert res["results"] == {"ok": ["정상"]}
