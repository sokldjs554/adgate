"""셋업 어시스턴트의 측정 묶음(docs/EVAL_SETUP.md). 사례 하나 = 시나리오 하나, 사례 안에서 팔(arm) 여럿을 같은 클라이언트로 돌린다.

* ``campaign``(sonnet): 교사 강제 추출 + 닫힌 고리 에이전트(A1) + 단일 호출(B2). ``campaign``(haiku): 교사 강제 + 닫힌 고리(A2).
* ``campaign_reply``(sonnet): 닫힌 고리에 LLM 답변 다듬기를 켠 12건 부분집합(D).
* 오프라인 팔(규칙 추출기 B1, 오라클 C)은 모델이 필요 없어 ``offline`` 이 돌린다.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ..eval.metrics import ratio
from ..llm import CaseOutcome, LLMClient
from ..suites import Driver, Suite, Variant, register
from .evaluate import (
    aggregate_closed,
    aggregate_teacher,
    heuristic_client,
    oracle_client,
    run_baseline,
    run_scenario,
    score_baseline,
    score_run,
    standin_client,
    teacher_forced,
)
from .scenarios import Scenario, load_scenarios

DATA = Path(__file__).resolve().parents[3] / "data" / "setup"
POLISH_SUBSET = tuple(f"S-{p}0{n}" for p in "CDEFGH" for n in (1, 2))   # 사전등록: test 의 각 조각 첫 두 건
VARIANTS = {
    "sonnet": Variant("sonnet", "claude-sonnet-5-5", "A1·B2·D"),
    "haiku": Variant("haiku", "claude-haiku-5-5", "A2"),
}


def load_split(root: Path | None = None) -> tuple[list[Scenario], list[Scenario]]:
    base = root or DATA
    return load_scenarios(base / "dev.jsonl"), load_scenarios(base / "test.jsonl")


def _by_split(outcomes: list[CaseOutcome]) -> dict[str, list[CaseOutcome]]:
    dev, test = load_split()
    dev_ids, test_ids = {s.id for s in dev}, {s.id for s in test}
    return {"dev": [o for o in outcomes if o.case in dev_ids], "test": [o for o in outcomes if o.case in test_ids]}


def _score(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
    res: dict[str, Any] = {"note": "dev 는 설계자가 보았다(헤드라인 아님). 헤드라인은 test 의 첫 접촉이다."}
    for split, sel in _by_split(outcomes).items():
        ok = [o for o in sel if o.result is not None]
        block: dict[str, Any] = {"cases": len(sel), "failed": [o.case for o in sel if o.result is None]}
        block["teacher_forced"] = aggregate_teacher([r for o in ok for r in o.result["teacher"]]) if ok else None
        block["closed_loop"] = aggregate_closed([o.result["closed"] for o in ok]) if ok else None
        base = [o.result["baseline"] for o in ok if "baseline" in o.result]
        if base:
            agg = aggregate_closed(base)
            agg["ready_with_errors"] = ratio(sum(bool(b.get("ready_with_errors")) for b in base), len(base))
            block["single_shot"] = agg
        res[split] = block
    return res


def _analyze(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
    """틀린 시나리오의 목록: 닫힌 고리에서 상태가 어긋났거나 슬롯이 정답과 다른 시나리오(무엇이 빠지고·틀리고·남았는가), 교사 강제에서 슬롯·의도를 틀린 턴."""
    dev, test = load_split()
    sc_by_id = {s.id: s for s in [*dev, *test]}
    split = {**{s.id: "dev" for s in dev}, **{s.id: "test" for s in test}}
    closed_fail: list[dict[str, Any]] = []
    turn_miss: list[dict[str, Any]] = []
    confusion: Counter[str] = Counter()
    for o in outcomes:
        if o.result is None:
            continue
        sc = sc_by_id[o.case]
        c = o.result.get("closed")
        if c and not (c["status_ok"] and c["exact"] and not c["forbidden"]):
            closed_fail.append({
                "id": sc.id, "split": split[sc.id], "traps": sc.traps, "expect_status": c["expect_status"], "status": c["status"], "diff": c["diff"],
                "forbidden": c["forbidden"], "errors": c["errors"], "expected_errors": sc.expect.validation_errors, "issue_codes": c["issue_codes"],
                "redundant_questions": c["redundant_questions"],
            })
        for r in o.result.get("teacher", []):
            # 금지 값은 정정 *전*의 옛 값일 수 있어 그 턴의 정답에도 있을 수 있다(오라클도 센다) → 정답과 다른 추출(fp)일 때만 틀린 턴으로 본다
            if r["fp"] or r["fn"] or not r["intent_ok"]:
                turn_miss.append({
                    "id": r["id"], "split": split[r["id"]], "turn": r["turn"], "utterance": sc.turns[r["turn"] - 1].user, "fp": r["fp"], "fn": r["fn"],
                    "intent": r["intent"], "gold_intent": r["gold_intent"], "forbidden": r["forbidden"], "ungrounded": r["ungrounded_reasons"],
                    "traps": r["traps"],
                })
            if not r["intent_ok"]:
                confusion[f"{r['gold_intent']}→{r['intent']}"] += 1
    return {"closed_failures": closed_fail, "teacher_turn_misses": turn_miss, "intent_confusions": dict(confusion), "failed_cases": [o.case for o in outcomes if o.result is None]}


@register("campaign")
def campaign() -> Suite:
    dev, test = load_split()
    scenarios = [*dev, *test]

    def cases(variant: str) -> list[tuple[str, Any]]:
        arms = ("teacher", "closed", "baseline") if variant == "sonnet" else ("teacher", "closed")
        return [(sc.id, {"sc": sc, "arms": arms}) for sc in scenarios]

    def driver(variant: str) -> Driver:
        def run(payload: dict[str, Any], client: LLMClient) -> dict[str, Any]:
            sc: Scenario = payload["sc"]
            out: dict[str, Any] = {}
            if "teacher" in payload["arms"]:
                out["teacher"] = teacher_forced(client, sc)
            if "closed" in payload["arms"]:
                out["closed"] = score_run(sc, run_scenario(client, sc))
            if "baseline" in payload["arms"]:
                out["baseline"] = score_baseline(sc, run_baseline(client, sc))
            return out

        return run

    return Suite(
        "campaign", "셋업 어시스턴트: 교사 강제 추출 + 닫힌 고리 + 단일 호출(비교)", "docs/EVAL_SETUP.md", VARIANTS, cases, driver,
        lambda payload: standin_client(payload["sc"]), _score, offline=offline, check_case=lambda: (dev[0].id, {"sc": dev[0], "arms": ("teacher",)}),
        analyze=_analyze,
    )


@register("campaign_reply")
def campaign_reply() -> Suite:
    _, test = load_split()
    subset = [sc for sc in test if sc.id in POLISH_SUBSET]

    def cases(variant: str) -> list[tuple[str, Any]]:
        return [(sc.id, {"sc": sc}) for sc in subset]

    def driver(variant: str) -> Driver:
        def run(payload: dict[str, Any], client: LLMClient) -> dict[str, Any]:
            sc: Scenario = payload["sc"]
            return {"closed": score_run(sc, run_scenario(client, sc, polish=True))}

        return run

    def score(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
        ok = [o for o in outcomes if o.result is not None]
        scores = [o.result["closed"] for o in ok]
        turns = sum(s["turns"] for s in scores)
        fb = sum(s["fallbacks"] for s in scores)
        return {
            "cases": len(outcomes), "failed": [o.case for o in outcomes if o.result is None],
            "closed_loop": aggregate_closed(scores) if scores else None,
            "reply": {"turns": turns, "verified": ratio(turns - fb, turns) if turns else None, "fallbacks": fb,
                      "fallback_reasons": dict(sum((Counter(s.get("fallback_reasons", {})) for s in scores), Counter()))},
        }

    return Suite(
        "campaign_reply", "셋업 어시스턴트: LLM 답변 다듬기와 답변 검증(12건)", "docs/EVAL_SETUP.md", {"sonnet": VARIANTS["sonnet"]}, cases, driver,
        lambda payload: standin_client(payload["sc"]), score,
    )


def offline() -> dict[str, Any]:
    """모델 없는 팔: B1 규칙 추출기, C 오라클. 같은 에이전트 틀(근거 검사·검증기·정책)에 넣는다."""
    dev, test = load_split()
    out: dict[str, Any] = {}
    for arm, make in (("B1_heuristic", lambda sc: heuristic_client()), ("C_oracle", oracle_client)):
        out[arm] = {}
        for split, items in (("dev", dev), ("test", test)):
            teacher = [r for sc in items for r in teacher_forced(make(sc), sc)]
            closed = [score_run(sc, run_scenario(make(sc), sc)) for sc in items]
            out[arm][split] = {"cases": len(items), "teacher_forced": aggregate_teacher(teacher), "closed_loop": aggregate_closed(closed)}
    return out
