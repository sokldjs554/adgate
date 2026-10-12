"""셋업 대화 평가 시나리오: 형식, 정답 상태의 누적 계산, 일관성 검사.

한 시나리오 = 광고주의 *정해진* 발화 몇 개(열린 고리: 어시스턴트가 뭐라고 답하든 다음 발화는 같다) + 정답.
정답은 **발화마다 추출되어야 하는 슬롯**(``extract``)과 마지막 상태(``expect``)다. 발화 t 직전의 정답 상태는 ``extract`` 를 t-1 까지 누적해 *계산*한다
(저자가 따로 옮겨 적지 않으므로 어긋나지 않는다). 정정은 뒤의 값이 앞의 값을 덮는다.

일관성 검사는 정답이 *옳은지*는 판단하지 않고, 형식·인용·검증기와의 정합을 본다: 인용이 그 발화에 있는가, 값이 슬롯 형식에 맞는가,
누적한 마지막 상태에 검증기를 돌린 오류 코드가 ``expect.validation_errors`` 와 같은가, ``status`` 가 마지막 상태와 모순되지 않는가.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .rules import ERROR_CODES, missing, validate
from .schema import SLOTS, SlotError, normalize, parse_date

Intent = Literal["provide", "correction", "confirm", "deny", "question", "off_topic", "injection"]
Status = Literal["ready", "needs_input", "invalid", "refused"]
TRAPS = (
    "plain", "vague", "correction", "contradiction", "injection", "out_of_scope", "unrealistic_budget", "unsupported_category", "unverified",
    "underage", "relative_date", "derived_budget", "missing_landing", "multi_slot", "typo_informal",
)


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str = Field(min_length=1, max_length=600)


class Extract(BaseModel):
    """발화 ``turn``(1부터) 에서 추출되어야 하는 슬롯 하나. ``quote`` 는 그 발화의 연속된 부분 문자열이다."""

    model_config = ConfigDict(extra="forbid")

    turn: int = Field(ge=1)
    path: str
    value: Any
    quote: str = Field(min_length=1)
    derived: bool = False


class Forbidden(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    value: Any


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status
    validation_errors: list[str] = Field(default_factory=list)   # 마지막 누적 상태에서 검증기가 내야 하는 오류 코드(경고 제외)
    turn_intents: list[Intent]                                    # 발화마다 하나
    forbidden: list[Forbidden] = Field(default_factory=list)      # 주입·오해로 들어가면 안 되는 값
    confirm_turn: int | None = None                               # 광고주가 최종 요약을 확인하는 발화(1부터). ready 일 때 필수


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    persona: str = Field(min_length=3, max_length=300)
    today: str
    verified: bool = True
    turns: list[Turn] = Field(min_length=2, max_length=8)
    extract: list[Extract]
    expect: Expect
    traps: list[str] = Field(min_length=1)


def load_scenarios(path: str | Path) -> list[Scenario]:
    out: list[Scenario] = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(Scenario.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"{path}:{n}: {str(exc)[:300]}") from exc
    return out


def state_after(sc: Scenario, turn: int) -> dict[str, Any]:
    """발화 ``turn``(1부터)까지의 정답 상태. 뒤의 추출이 앞의 값을 덮는다. ``turn=0`` 은 빈 상태."""
    state: dict[str, Any] = {}
    for e in sorted((e for e in sc.extract if e.turn <= turn), key=lambda e: e.turn):
        state[e.path] = e.value
    return state


def final_state(sc: Scenario) -> dict[str, Any]:
    return state_after(sc, len(sc.turns))


def gold_for_turn(sc: Scenario, turn: int) -> list[Extract]:
    return [e for e in sc.extract if e.turn == turn]


def errors_of(sc: Scenario) -> list[str]:
    """마지막 상태에 검증기를 돌린 오류 코드(경고 제외, 정렬)."""
    issues = validate(final_state(sc), today=parse_date(sc.today), verified=sc.verified)
    return sorted({i.code for i in issues if i.severity == "error"})


def validate_scenarios(items: list[Scenario]) -> list[str]:
    errs: list[str] = []
    seen: set[str] = set()
    for sc in items:
        if sc.id in seen:
            errs.append(f"{sc.id}: 중복 id")
        seen.add(sc.id)
        try:
            today = parse_date(sc.today)
        except SlotError as exc:
            errs.append(f"{sc.id}: today {exc}")
            continue
        n = len(sc.turns)
        if len(sc.expect.turn_intents) != n:
            errs.append(f"{sc.id}: turn_intents 가 {len(sc.expect.turn_intents)}개(발화 {n}개와 같아야 함)")
        bad_traps = [t for t in sc.traps if t not in TRAPS]
        if bad_traps:
            errs.append(f"{sc.id}: 알 수 없는 함정 {bad_traps}")
        for e in sc.extract:
            if e.turn > n:
                errs.append(f"{sc.id}: extract 의 turn {e.turn} 이 발화 수 {n} 을 넘음")
                continue
            if e.quote not in sc.turns[e.turn - 1].user:
                errs.append(f"{sc.id}: 인용 {e.quote!r} 이(가) {e.turn}번째 발화에 없음")
            if e.path not in SLOTS:
                errs.append(f"{sc.id}: 알 수 없는 슬롯 {e.path}")
                continue
            try:
                e.value = normalize(e.path, e.value)
            except SlotError as exc:
                errs.append(f"{sc.id}: {exc}")
        # 정정이 아닌데 같은 슬롯을 두 번 추출하면 의심
        firsts: dict[str, int] = {}
        for e in sorted(sc.extract, key=lambda x: x.turn):
            if e.path in firsts and firsts[e.path] != e.turn and "correction" not in sc.expect.turn_intents and "contradiction" not in sc.traps:
                errs.append(f"{sc.id}: {e.path} 를 여러 발화에서 추출했는데 정정(correction/contradiction)이 아님")
            firsts.setdefault(e.path, e.turn)
        for f in sc.expect.forbidden:
            if f.path not in SLOTS:
                errs.append(f"{sc.id}: forbidden 의 알 수 없는 슬롯 {f.path}")
            elif final_state(sc).get(f.path) == f.value:
                errs.append(f"{sc.id}: forbidden 값 {f.path}={f.value!r} 이(가) 정답 상태에 들어 있음")
        bad_codes = [c for c in sc.expect.validation_errors if c not in ERROR_CODES]
        if bad_codes:
            errs.append(f"{sc.id}: 알 수 없는 오류 코드 {bad_codes}")
        got = errors_of(sc)
        if got != sorted(set(sc.expect.validation_errors)):
            errs.append(f"{sc.id}: 마지막 상태의 검증 오류 {got} 가 expect.validation_errors {sorted(set(sc.expect.validation_errors))} 와 다름")
        state = final_state(sc)
        miss = missing(state)
        st = sc.expect.status
        if st == "ready":
            if miss or sc.expect.validation_errors:
                errs.append(f"{sc.id}: ready 인데 비어 있는 필수 슬롯 {miss} 또는 검증 오류가 있음")
            if sc.expect.confirm_turn is None or not 1 <= sc.expect.confirm_turn <= n:
                errs.append(f"{sc.id}: ready 는 confirm_turn(광고주가 확인하는 발화)이 필요함")
            elif sc.expect.turn_intents[sc.expect.confirm_turn - 1] != "confirm":
                errs.append(f"{sc.id}: confirm_turn 의 intent 가 confirm 이 아님")
        elif st == "needs_input" and not miss:
            errs.append(f"{sc.id}: needs_input 인데 비어 있는 필수 슬롯이 없음")
        elif st == "invalid" and not sc.expect.validation_errors:
            errs.append(f"{sc.id}: invalid 인데 validation_errors 가 비어 있음")
        elif st == "refused" and "unsupported_category" not in sc.traps and "out_of_scope" not in sc.traps and "unverified" not in sc.traps:
            errs.append(f"{sc.id}: refused 는 unsupported_category/out_of_scope/unverified 함정과 함께 쓴다")
        if sc.expect.status != "ready" and sc.expect.confirm_turn is not None:
            errs.append(f"{sc.id}: ready 가 아니면 confirm_turn 을 쓰지 않는다")
        if today < date(2026, 1, 1) or today > date(2027, 12, 31):
            errs.append(f"{sc.id}: today 가 2026–2027 밖")
    return errs
