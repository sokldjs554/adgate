"""셋업 대화 평가: 정해진 발화에 대한 두 가지 측정과 비교 기준.

* **교사 강제(teacher-forced) 추출 평가** — 발화 t 의 입력을 *정답 상태*(t-1 까지)에서 만들어 준다. 이전 턴의 실수가 이어지지 않아 추출기만의 정확도를 본다.
  지표: 슬롯 (경로, 값) 정밀도·재현율, 근거 검사 전에 모델이 제안한 슬롯 중 *근거 없는* 것(인용이 발화에 없음 등)의 비율, 금지 값을 추출한 비율, 의도 정확도.
* **닫힌 고리(closed-loop) 대화 평가** — 광고주 발화는 정해져 있고(열린 고리) 어시스턴트가 자기 상태로 끝까지 간다. 마지막 상태·상태 판정·검증 오류·금지 값을 정답과 견준다.
  지표: 상태 일치, 최종 슬롯 F1, 검증 오류 일치, 금지 값 유입, **거짓 ready**(정답이 ready 가 아닌데 ready), 놓친 ready, 이미 준 정보를 다시 묻기, 질문 수 상한 위반.
* 비교 기준: 규칙(정규식) 추출기 + 같은 에이전트(근거 검사·검증기·정책), 단일 호출 LLM(검증기 없음).
* 오라클: 정답 추출을 그대로 돌려주는 클라이언트 — 정책·검증기·시나리오의 **정합**을 본다(모델 정확도가 아니다).

사양에 없는 공백(상태 refused/invalid 구분 등)은 ``lenient`` 점수를 따로 낸다: refused ≈ invalid.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..eval.metrics import ratio
from ..llm import LLMClient, LLMError, LLMMissing, LLMTask, ScriptedClient
from ..llm.core import Part
from .agent import WEEKDAYS, Agent, Session, SlotValue, TurnResult
from .rules import missing, validate
from .scenarios import Scenario, final_state, state_after
from .schema import SLOTS, parse_date
from .tasks import baseline_input, baseline_task


# --------------------------------------------------------------------------- 실행
@dataclass
class Run:
    scenario: str
    results: list[TurnResult]
    session: Session
    error: str | None = None

    @property
    def final(self) -> TurnResult:
        return self.results[-1]


def run_scenario(client: LLMClient, sc: Scenario, *, polish: bool = False) -> Run:
    session = Session(today=parse_date(sc.today), verified=sc.verified)
    agent = Agent(client, polish=polish)
    results: list[TurnResult] = []
    for turn in sc.turns:
        results.append(agent.step(session, turn.user))
    return Run(sc.id, results, session)


def _final_status(r: TurnResult) -> str:
    return "needs_input" if r.status == "awaiting_confirm" else r.status


def _lenient(status: str) -> str:
    return "invalid" if status == "refused" else status


def _user_state(session: Session) -> dict[str, Any]:
    return {p: v.value for p, v in session.slots.items() if v.source != "assumed"}


def score_run(sc: Scenario, run: Run) -> dict[str, Any]:
    gold = final_state(sc)
    got = _user_state(run.session)
    tp = sum(1 for p, v in got.items() if gold.get(p) == v)
    fp, fn = len(got) - tp, sum(1 for p, v in gold.items() if got.get(p) != v)
    status = _final_status(run.final)
    exp = sc.expect.status
    errors = sorted({i.code for i in run.final.issues if i.severity == "error"})
    # 금지 값: 그 시점의 정답 상태에도 없는 금지 값이 어느 턴의 상태에든 들어갔는가(정정으로 잠시 맞았던 옛 값은 그 시점의 정답이라 세지 않는다)
    forbidden_hit = [
        f"{f.path}={f.value}" for f in sc.expect.forbidden
        if any(r.state.get(f.path) == f.value and state_after(sc, t).get(f.path) != f.value for t, r in enumerate(run.results, start=1))
    ]
    redundant = 0
    for t, r in enumerate(run.results, start=1):
        have = state_after(sc, t - 1)
        redundant += sum(1 for q in r.questions if q in have)
    intents = sum(r.intent == g for r, g in zip(run.results, sc.expect.turn_intents, strict=True))
    return {
        "id": sc.id, "traps": sc.traps, "expect_status": exp, "status": status,
        "status_ok": status == exp, "status_lenient_ok": _lenient(status) == _lenient(exp),
        "slots": {"tp": tp, "fp": fp, "fn": fn}, "exact": fp == 0 and fn == 0,
        "errors_ok": errors == sorted(set(sc.expect.validation_errors)), "errors": errors,
        "forbidden": forbidden_hit, "false_ready": status == "ready" and exp != "ready", "missed_ready": status != "ready" and exp == "ready",
        "redundant_questions": redundant, "too_many_questions": sum(len(r.questions) > 2 for r in run.results),
        "intent_ok": intents, "turns": len(run.results),
        "rejected": dict(Counter(x.reason for r in run.results for x in r.rejected)),
        "proposed": sum(r.proposed for r in run.results), "fallbacks": sum(r.used_fallback for r in run.results),
        "fallback_reasons": dict(Counter(r.fallback_reason for r in run.results if r.fallback_reason)),
        "llm_errors": sum(r.llm_error is not None for r in run.results),
    }


# --------------------------------------------------------------------------- 교사 강제 추출
def _session_at(sc: Scenario, turn: int) -> Session:
    """정답 상태(turn 까지)를 상태로 가진 세션 + 그 상태에서 어시스턴트가 했을 말(``last_assistant``)."""
    s = Session(today=parse_date(sc.today), verified=sc.verified)
    derived = {(e.path, e.turn) for e in sc.extract if e.derived}
    for e in sorted((e for e in sc.extract if e.turn <= turn), key=lambda e: e.turn):
        s.slots[e.path] = SlotValue(e.value, "derived" if (e.path, e.turn) in derived else "user", e.quote, e.turn)
    s.turn = turn
    return s


def teacher_forced(client: LLMClient, sc: Scenario) -> list[dict[str, Any]]:
    agent = Agent(client)
    out: list[dict[str, Any]] = []
    for t, turn in enumerate(sc.turns, start=1):
        s = _session_at(sc, t - 1)
        if t > 1:
            agent.respond(s, "provide", [], "", changed=False)         # 직전 답변을 재현한다(상태만으로 정해진다)
        intent, accepted, _retract, rejected, proposed, err = agent.understand(s, turn.user)
        gold = {e.path: e.value for e in sc.extract if e.turn == t}
        got = {a["path"]: a["value"] for a in accepted}
        tp = sum(1 for p, v in got.items() if gold.get(p) == v)
        forbidden = [f for f in sc.expect.forbidden if got.get(f.path) == f.value]
        out.append({
            "id": sc.id, "turn": t, "gold": len(gold), "got": len(got), "tp": tp, "fp": len(got) - tp, "fn": sum(1 for p, v in gold.items() if got.get(p) != v),
            "proposed": proposed, "ungrounded": len(rejected), "ungrounded_reasons": dict(Counter(r.reason for r in rejected)),
            "forbidden": len(forbidden), "intent_ok": intent == sc.expect.turn_intents[t - 1], "intent": intent, "gold_intent": sc.expect.turn_intents[t - 1],
            "llm_error": err, "traps": sc.traps,
        })
    return out


# --------------------------------------------------------------------------- 집계
def _sum(rows: list[dict[str, Any]], key: str) -> int:
    return sum(int(r[key]) for r in rows)


def aggregate_closed(scores: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(scores)
    tp, fp, fn = (sum(s["slots"][k] for s in scores) for k in ("tp", "fp", "fn"))
    prec, rec = tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0
    turns = sum(s["turns"] for s in scores)
    by_trap: dict[str, dict[str, Any]] = {}
    for trap in sorted({t for s in scores for t in s["traps"]}):
        sel = [s for s in scores if trap in s["traps"]]
        by_trap[trap] = {"n": len(sel), "status_ok": ratio(sum(s["status_ok"] for s in sel), len(sel)), "exact": ratio(sum(s["exact"] for s in sel), len(sel))}
    exp_ready = [s for s in scores if s["expect_status"] == "ready"]
    not_ready = [s for s in scores if s["expect_status"] != "ready"]
    return {
        "n": n,
        "status_ok": ratio(sum(s["status_ok"] for s in scores), n),
        "status_lenient_ok": ratio(sum(s["status_lenient_ok"] for s in scores), n),
        "errors_ok": ratio(sum(s["errors_ok"] for s in scores), n),
        "slots": {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0, "tp": tp, "fp": fp, "fn": fn},
        "exact": ratio(sum(s["exact"] for s in scores), n),
        "false_ready": ratio(sum(s["false_ready"] for s in not_ready), len(not_ready)),
        "missed_ready": ratio(sum(s["missed_ready"] for s in exp_ready), len(exp_ready)),
        "forbidden_scenarios": ratio(sum(bool(s["forbidden"]) for s in scores), n),
        "redundant_questions": _sum(scores, "redundant_questions"), "too_many_questions": _sum(scores, "too_many_questions"),
        "intent": None if all(s["intent_ok"] is None for s in scores) else ratio(sum(s["intent_ok"] or 0 for s in scores), turns),
        "ungrounded_proposals": ratio(sum(sum(s["rejected"].values()) for s in scores), max(1, _sum(scores, "proposed"))),
        "reply_fallbacks": _sum(scores, "fallbacks"), "llm_errors": _sum(scores, "llm_errors"),
        "fallback_reasons": dict(sum((Counter(s.get("fallback_reasons", {})) for s in scores), Counter())),
        "by_trap": by_trap,
        "failures": [{"id": s["id"], "expect": s["expect_status"], "got": s["status"], "forbidden": s["forbidden"], "errors": s["errors"]} for s in scores if not s["status_ok"] or s["forbidden"]],
    }


def aggregate_teacher(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp, fp, fn = _sum(rows, "tp"), _sum(rows, "fp"), _sum(rows, "fn")
    prec, rec = tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0
    by_trap: dict[str, dict[str, Any]] = {}
    for trap in sorted({t for r in rows for t in r["traps"]}):
        sel = [r for r in rows if trap in r["traps"]]
        a, b, c = _sum(sel, "tp"), _sum(sel, "fp"), _sum(sel, "fn")
        by_trap[trap] = {"turns": len(sel), "recall": round(a / (a + c), 4) if a + c else None, "precision": round(a / (a + b), 4) if a + b else None}
    return {
        "turns": len(rows),
        "slots": {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0, "tp": tp, "fp": fp, "fn": fn},
        "intent": ratio(sum(r["intent_ok"] for r in rows), len(rows)),
        "ungrounded_proposals": ratio(_sum(rows, "ungrounded"), max(1, _sum(rows, "proposed"))),
        "forbidden_extracted": _sum(rows, "forbidden"),
        "llm_errors": sum(r["llm_error"] is not None for r in rows),
        "by_trap": by_trap,
    }


# --------------------------------------------------------------------------- 오라클 클라이언트
def oracle_client(sc: Scenario) -> ScriptedClient:
    """정답 추출·의도를 그대로 돌려준다. 발화 본문으로 몇 번째 턴인지 찾는다."""
    by_user = {t.user: i + 1 for i, t in enumerate(sc.turns)}

    def handler(task: LLMTask, parts: list[Part]) -> dict[str, Any]:
        if task.name != "campaign.extract":
            raise LLMError("오라클은 추출만 안다")
        payload = json.loads(str(parts[0]))
        t = by_user[payload["utterance"]]
        derived = {(e.path, e.turn) for e in sc.extract if e.derived}
        slots = [{"path": e.path, "value_json": json.dumps(e.value, ensure_ascii=False), "quote": e.quote, "derived": (e.path, e.turn) in derived}
                 for e in sc.extract if e.turn == t]
        return {"intent": sc.expect.turn_intents[t - 1], "slots": slots, "retract": []}

    return ScriptedClient(handler)


# --------------------------------------------------------------------------- 규칙(정규식) 추출기: 모델 없는 비교 기준
_UNIT = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, "천": 1_000, "백": 100}
_MONEY = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|만|천|백)?\s*원?")
_ONLY_UNIT = re.compile(r"(?<![0-9가-힣])(만|천)\s*원")
_OBJ = (
    ("app_install", r"앱\s*설치|다운로드|설치"), ("lead", r"상담|문의|견적|예약|신청\s*받"), ("conversion", r"판매|주문|매출|구매|팔고|팔리"),
    ("traffic", r"방문|클릭|유입|트래픽"), ("awareness", r"알리|노출|인지도|홍보"),
)
_CAT = (
    ("adult", r"성인용품|성인\s*콘텐츠"), ("gambling", r"도박|카지노|베팅"), ("loan", r"대출|카드론"), ("invest", r"주식|펀드|투자"),
    ("cosmetic", r"화장품|스킨케어"), ("supplement", r"건강기능식품|영양제"), ("education", r"학원|강의|수업"), ("app", r"앱|소프트웨어"),
    ("ecommerce", r"쇼핑몰|의류|옷|식품|생활용품|스토어"), ("general", r"카페|숙박|헬스장|미용실|수리"),
)
_EVENT = (("purchase", r"구매"), ("signup", r"회원가입|가입"), ("add_to_cart", r"장바구니"), ("install", r"앱\s*설치|설치"), ("inquiry", r"상담|문의"))
_REGIONS = ("서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "세종", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주")


def _money(text: str, start: int = 0) -> int | None:
    m = _MONEY.search(text, start)
    if m:
        num = float(m.group(1).replace(",", ""))
        unit = _UNIT.get(m.group(2) or "", 1)
        value = int(num * unit)
        if value >= 100:
            return value
    m = _ONLY_UNIT.search(text, start)
    return _UNIT[m.group(1)] if m else None


def _next_weekday(today: date, wd: int, *, next_week: bool) -> date:
    base = today + timedelta(days=(7 - today.weekday())) if next_week else today
    return base + timedelta(days=(wd - base.weekday()) % 7)


def heuristic_extract(utterance: str, today: date) -> dict[str, Any]:
    """키워드·정규식만으로 슬롯을 뽑는다(모델 없음). 비교 기준이라 일부러 단순하게 두었다."""
    slots: list[dict[str, Any]] = []

    def add(path: str, value: Any, quote: str, derived: bool = False) -> None:
        slots.append({"path": path, "value_json": json.dumps(value, ensure_ascii=False), "quote": quote, "derived": derived})

    for m in re.finditer(r"(하루|일\s*예산|매일|일)\s*(?:에|은|는)?\s*", utterance):
        v = _money(utterance, m.end())
        if v is not None:
            add("daily_budget_krw", v, utterance[m.start():m.end() + 8].split("원")[0] + ("원" if "원" in utterance[m.end():m.end() + 8] else "") or m.group())
            break
    else:
        mm = re.search(r"(?:한\s*달|월)\s*(?:에|은|는)?\s*", utterance)
        if mm and (v := _money(utterance, mm.end())) is not None and not re.search(r"까지|종료", utterance):
            add("daily_budget_krw", v // 30 // 100 * 100, utterance[mm.start():mm.end() + 8], True)
    mt = re.search(r"(?:총|전체)\s*", utterance)
    if mt and (v := _money(utterance, mt.end())) is not None:
        add("total_budget_krw", v, utterance[mt.start():mt.end() + 8])
    for value, pat in _OBJ:
        if (mo := re.search(pat, utterance)):
            add("objective", value, mo.group())
            break
    for value, pat in _CAT:
        if (mo := re.search(pat, utterance)):
            add("ad_category", value, mo.group())
            break
    if (mo := re.search(r"https?://[^\s,]+", utterance)):
        url = mo.group().rstrip(".")
        if url.startswith("https://"):
            add("landing_url", url, url)
    if re.search(r"전환|기준|잡아", utterance):
        for value, pat in _EVENT:
            if (mo := re.search(pat, utterance)):
                add("conversion_event", value, mo.group())
                break
    if (mo := re.search(r"(\d)0\s*[~\-]\s*(\d)0대", utterance)):
        add("audience.age_min", int(mo.group(1)) * 10, mo.group(), True)
        add("audience.age_max", int(mo.group(2)) * 10 + 9, mo.group(), True)
    elif (mo := re.search(r"(\d)0대", utterance)):
        add("audience.age_min", int(mo.group(1)) * 10, mo.group(), True)
        add("audience.age_max", int(mo.group(1)) * 10 + 9, mo.group(), True)
    if (mo := re.search(r"여성|여자", utterance)):
        add("audience.genders", ["female"], mo.group())
    elif (mo := re.search(r"남성|남자", utterance)):
        add("audience.genders", ["male"], mo.group())
    if "수도권" in utterance:
        add("audience.regions", ["서울", "경기", "인천"], "수도권", True)
    else:
        found = [r for r in _REGIONS if r in utterance]
        if found:
            add("audience.regions", found, found[0])
    if "내일" in utterance:
        add("start_date", (today + timedelta(days=1)).isoformat(), "내일", True)
    elif (mo := re.search(r"(?:다음\s*주|담주)\s*([월화수목금토일])", utterance)):
        add("start_date", _next_weekday(today, WEEKDAYS.index(mo.group(1)), next_week=True).isoformat(), mo.group(), True)
    intent = "provide"
    if _injection(utterance):
        intent = "injection"
        slots = [s for s in slots if not _injection(s["quote"])]
    elif re.search(r"보장|경쟁사|날씨|맛집|농담", utterance):
        intent = "off_topic"
    elif not slots and re.search(r"^(?:네|넵|예|응|ㅇㅇ|좋아요|맞아요|그렇게|그대로|진행)", utterance.strip()):
        intent = "confirm"
    elif utterance.strip().endswith("?"):
        intent = "question"
    return {"intent": intent, "slots": slots, "retract": []}


def _injection(text: str) -> bool:
    return bool(re.search(r"무시|관리자|시스템|검증.{0,4}(?:건너|생략)", text))


def heuristic_client() -> ScriptedClient:
    def handler(task: LLMTask, parts: list[Part]) -> dict[str, Any]:
        payload = json.loads(str(parts[0]))
        return heuristic_extract(payload["utterance"], date.fromisoformat(payload["today"]))

    return ScriptedClient(handler)


# --------------------------------------------------------------------------- 단일 호출 LLM 비교 기준(검증기·근거 검사 없음)
@dataclass
class BaselineRun:
    scenario: str
    status: str
    slots: dict[str, Any]
    error: str | None = None
    issues: list[str] = field(default_factory=list)


def run_baseline(client: LLMClient, sc: Scenario) -> BaselineRun:
    dialog = [{"role": "advertiser", "text": t.user} for t in sc.turns]
    try:
        out = client.call(baseline_task(), baseline_input(dialog, sc.today, sc.verified)).response
    except LLMMissing:
        raise
    except LLMError as exc:
        return BaselineRun(sc.id, "needs_input", {}, type(exc).__name__)
    slots: dict[str, Any] = {}
    for it in out.get("slots", []):
        path = str(it.get("path", ""))
        if path in SLOTS:
            try:
                from .agent import parse_value
                from .schema import normalize

                slots[path] = normalize(path, parse_value(str(it.get("value_json", "")), path))
            except Exception:  # noqa: BLE001 — 형식이 맞지 않는 값은 버린다(비교 기준에도 최소한의 형식 검사는 둔다)
                continue
    status = str(out.get("status", "needs_input"))
    issues = [i.code for i in validate(_with_defaults(slots), today=parse_date(sc.today), verified=sc.verified) if i.severity == "error"]
    return BaselineRun(sc.id, status, slots, None, sorted(set(issues)))


def _with_defaults(slots: dict[str, Any]) -> dict[str, Any]:
    from .schema import ASSUMABLE_DEFAULTS

    spec = dict(slots)
    for p, d in ASSUMABLE_DEFAULTS.items():
        spec.setdefault(p, list(d) if isinstance(d, list) else d)
    return spec


def score_baseline(sc: Scenario, run: BaselineRun) -> dict[str, Any]:
    gold = final_state(sc)
    tp = sum(1 for p, v in run.slots.items() if gold.get(p) == v)
    fp, fn = len(run.slots) - tp, sum(1 for p, v in gold.items() if run.slots.get(p) != v)
    exp = sc.expect.status
    forbidden = [f"{f.path}={f.value}" for f in sc.expect.forbidden if run.slots.get(f.path) == f.value]
    return {
        "id": sc.id, "traps": sc.traps, "expect_status": exp, "status": run.status, "status_ok": run.status == exp,
        "status_lenient_ok": _lenient(run.status) == _lenient(exp), "slots": {"tp": tp, "fp": fp, "fn": fn}, "exact": fp == 0 and fn == 0,
        "errors_ok": run.issues == sorted(set(sc.expect.validation_errors)), "errors": run.issues, "forbidden": forbidden,
        "false_ready": run.status == "ready" and exp != "ready", "missed_ready": run.status != "ready" and exp == "ready",
        "ready_with_errors": run.status == "ready" and bool(run.issues),
        "redundant_questions": 0, "too_many_questions": 0, "intent_ok": None, "turns": len(sc.turns), "rejected": {}, "proposed": 0, "fallbacks": 0,
        "llm_errors": int(run.error is not None),
    }


def scenario_missing(sc: Scenario) -> list[str]:
    return missing(final_state(sc))


Driver = Callable[[Scenario, LLMClient], Any]


def standin_client(sc: Scenario) -> ScriptedClient:
    """dry-run 용 대역: 추출은 오라클, 단일 호출은 정답 상태·status, 답변 다듬기는 템플릿 초안 그대로. 실제 모델이 아니라 호출 수·길이를 세기 위한 것이다."""
    oracle = oracle_client(sc)

    def handler(task: LLMTask, parts: list[Part]) -> dict[str, Any]:
        if task.name == "campaign.extract":
            return oracle.handler(task, parts)
        if task.name == "campaign.baseline":
            slots = [{"path": p, "value_json": json.dumps(v, ensure_ascii=False), "quote": "", "derived": False} for p, v in final_state(sc).items()]
            return {"status": sc.expect.status, "slots": slots, "reply": "정리했어요."}
        if task.name == "campaign.reply":
            return {"message": json.loads(str(parts[0]))["plan"]["draft"]}
        raise LLMError(task.name)

    return ScriptedClient(handler)
