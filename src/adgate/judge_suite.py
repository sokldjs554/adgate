"""회색지대 judge 측정 묶음(docs/EVAL_GRAY.md 3–5절, docs/JUDGE_GRAY.md).

* ``judge_gray`` — 변형 v1(JUDGE_LIVE 와 같은 프롬프트) · v2(정책 조항 검색)를 **같은 모델(claude-opus-5-5)·같은 effort(medium)** 로 소재당 한 번씩 부른다.
  사례 하나 = 회색지대 소재 하나. driver 는 결정론 경로 · judge · judge+REVIEW 상한 · judge+상한+신뢰도 문턱을 한 사례 안에서 돌린다
  (같은 호출은 한 번만 녹음되고 나머지는 재생된다 — 문턱은 후처리라 추가 호출이 없다).
* 판정 규칙 R1(E4)·R2(E5)에 쓰는 수치는 ``_score`` 가 산출물에서 그대로 계산한다.

결정론 경로의 첫 접촉(E1)은 이미 재어 보존했다(``artifacts/history/gray_first_contact.json``). 여기서 'full' 은 *그 뒤 수정을 거친 현재 엔진*이다 — judge 녹음은 엔진의
``already_found`` 를 입력으로 받으므로 엔진을 바꾸면 열쇠가 바뀐다. 그래서 엔진을 바꾸는 일을 모두 마친 뒤에 녹음한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ReviewConfig
from .eval import dataset, metrics
from .eval.dataset import Example
from .eval.metrics import ratio
from .eval.systems import Prediction
from .judge_llm import LLMJudge, PolicyIndex, ThresholdJudge, load_clauses
from .llm import CaseOutcome, LLMClient, LLMMissing, ScriptedClient
from .models import Finding
from .pipeline import review
from .policy import Policy, load_policy
from .suites import Driver, Suite, Variant, register

ROOT = Path(__file__).resolve().parents[2]
GRAY = ROOT / "data" / "gray.jsonl"
MODEL = "claude-opus-5-5"
THETA = 0.65       # 사전등록(EVAL_GRAY E5)이 이 셋을 보기 전에 정한 값
VARIANTS = {"v1": Variant("v1", MODEL, "JUDGE_LIVE 와 같은 프롬프트"), "v2": Variant("v2", MODEL, "정책 조항 검색(상위 4개 + 이미 발견된 규칙 + 공통 해석 규칙)")}
CONFIGS = ("full", "judge", "cap", "cap_theta")
_RANK = {"APPROVE": 0, "REVIEW": 1, "REJECT": 2}


def load_gray(path: Path | None = None) -> list[Example]:
    return dataset.load(path or GRAY)


def _pred(r: Any) -> dict[str, Any]:
    return {
        "outcome": r.outcome, "findings": [f.model_dump(mode="json") for f in r.findings], "escalations": list(r.escalations),
        "dropped": r.stats.dropped_ungrounded, "quote_mismatch": r.stats.quote_mismatch, "judge_failed": r.stats.judge_failed,
    }


def _to_prediction(d: dict[str, Any]) -> Prediction:
    return Prediction(d["outcome"], [Finding.model_validate(f) for f in d["findings"]], 0.0, d["quote_mismatch"], list(d["escalations"]), d["dropped"])


def run_case(ex: Example, client: LLMClient, policy: Policy, version: str, index: PolicyIndex | None) -> dict[str, Any]:
    judge = LLMJudge(client, policy, version, index)
    base = review(ex.creative, ReviewConfig(), policy)
    j = review(ex.creative, ReviewConfig(judge=judge, judge_cap_review=False), policy)
    c = review(ex.creative, ReviewConfig(judge=judge, judge_cap_review=True), policy)
    ct = review(ex.creative, ReviewConfig(judge=ThresholdJudge(judge, THETA), judge_cap_review=True), policy)
    if judge.missing:
        raise LLMMissing(f"{ex.id}: 녹음에 없는 judge 호출 {judge.missing}회")
    return {
        "full": _pred(base), "judge": _pred(j), "cap": _pred(c), "cap_theta": _pred(ct),
        "proposals": judge.last_proposals, "retrieved": judge.last_retrieved if version == "v2" else None,
    }


# --- 채점 -----------------------------------------------------------------------------------------------------------------------------


def effects(examples: list[Example], base: list[Prediction], other: list[Prediction]) -> dict[str, Any]:
    """judge 구성이 결정론 경로에 더한 것: 오탐(정답 APPROVE 를 건드림), 회수(결정론이 APPROVE 로 놓친 위험 소재를 되찾음), 위험 승인율 감소."""
    appr = [i for i, ex in enumerate(examples) if ex.gold.decision == "APPROVE"]
    risky_missed = [i for i, ex in enumerate(examples) if ex.gold.decision != "APPROVE" and base[i].outcome == "APPROVE"]
    risky = [i for i, ex in enumerate(examples) if ex.gold.decision != "APPROVE"]
    fp = [i for i in appr if other[i].outcome != base[i].outcome]
    fp_to_reject = [i for i in appr if other[i].outcome == "REJECT" and base[i].outcome != "REJECT"]
    caught = [i for i in risky_missed if other[i].outcome != "APPROVE"]
    ua_base = sum(base[i].outcome == "APPROVE" for i in risky)
    ua_other = sum(other[i].outcome == "APPROVE" for i in risky)
    worse = [i for i in range(len(examples)) if _RANK[other[i].outcome] > _RANK[base[i].outcome] and examples[i].gold.decision == "APPROVE"]
    return {
        "false_positive": ratio(len(fp), len(appr)), "false_positive_ids": [examples[i].id for i in fp],
        "false_positive_to_reject": [examples[i].id for i in fp_to_reject],
        "recovered": ratio(len(caught), len(risky_missed)), "recovered_ids": [examples[i].id for i in caught],
        "unsafe_approve_base": ratio(ua_base, len(risky)), "unsafe_approve_other": ratio(ua_other, len(risky)),
        "unsafe_approve_relative_drop": round((ua_base - ua_other) / ua_base, 4) if ua_base else None,
        "approve_made_worse": [examples[i].id for i in worse],
    }


def _gold_rules(ex: Example) -> set[str]:
    return {v.rule_id for v in ex.gold.violations}


def retrieval_recall(examples: list[Example], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """v2 검색 재현율: 정답 위반의 규칙 중 검색된 조항에 든 비율 (전체 / 결정론이 놓친 규칙만 / 규칙 조항만 따로)."""
    tot = hit = miss_tot = miss_hit = 0
    for ex in examples:
        r = results.get(ex.id)
        if not r or r.get("retrieved") is None:
            continue
        got = set(r["retrieved"])
        det = {f["rule_id"] for f in r["full"]["findings"] if f["severity"] != "info"}
        for rule in _gold_rules(ex):
            tot += 1
            hit += rule in got
            if rule not in det:
                miss_tot += 1
                miss_hit += rule in got
    return {"all": ratio(hit, tot), "missed_by_deterministic": ratio(miss_hit, miss_tot)}


def _score(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
    ex_by_id = {e.id: e for e in load_gray()}
    ok = [o for o in outcomes if o.result is not None]
    examples = [ex_by_id[o.case] for o in ok]
    results = {o.case: o.result for o in ok}
    preds: dict[str, list[Prediction]] = {c: [_to_prediction(o.result[c]) for o in ok] for c in CONFIGS}
    out: dict[str, Any] = {
        "variant": variant, "cases": len(outcomes), "failed": [o.case for o in outcomes if o.result is None], "theta": THETA,
        "metrics": {c: _headline(metrics.compute(examples, preds[c])) for c in CONFIGS},
        "effects": {c: effects(examples, preds["full"], preds[c]) for c in ("judge", "cap", "cap_theta")},
        "judge_unavailable": [e.id for e, p in zip(examples, preds["cap"], strict=True) if "judge_unavailable" in p.escalations],
        "judge_ungrounded": [e.id for e, p in zip(examples, preds["cap"], strict=True) if "judge_ungrounded" in p.escalations],
        "proposals": {"cases_with_any": sum(bool(results[e.id]["proposals"]) for e in examples), "total": sum(len(results[e.id]["proposals"]) for e in examples)},
        "slices": _slices(examples, preds),
    }
    if variant == "v2":
        out["retrieval"] = retrieval_recall(examples, results)
    return out


def _headline(m: dict[str, Any]) -> dict[str, Any]:
    keys = ("n", "accuracy", "unsafe_approve", "false_reject", "over_review", "hard_pass_through", "auto_rate", "auto_precision")
    return {**{k: m[k] for k in keys}, "rule_f1": m["rule"]["micro"]["f1"], "confusion": m["confusion"]}


def _slices(examples: list[Example], preds: dict[str, list[Prediction]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tag in ("clear", "normal", "gray", "disputed"):
        idx = [i for i, e in enumerate(examples) if tag in e.gold.tags]
        if idx:
            out[tag] = {c: {"n": len(idx), "accuracy": ratio(sum(preds[c][i].outcome == examples[i].gold.decision for i in idx), len(idx))} for c in ("full", "cap")}
    return out


def _standin(payload: dict[str, Any]) -> ScriptedClient:
    return ScriptedClient(lambda task, parts: {"findings": []})


@register("judge_gray")
def judge_gray() -> Suite:
    examples = load_gray()
    policy = load_policy()
    index = PolicyIndex(load_clauses(policy=policy))

    def cases(variant: str) -> list[tuple[str, Any]]:
        return [(e.id, {"ex": e}) for e in examples]

    def driver(variant: str) -> Driver:
        def run(payload: dict[str, Any], client: LLMClient) -> dict[str, Any]:
            return run_case(payload["ex"], client, policy, variant, index if variant == "v2" else None)

        return run

    return Suite(
        "judge_gray", "회색지대 judge: v1(JUDGE_LIVE 프롬프트) vs v2(정책 조항 검색) + 신뢰도 문턱 후처리", "docs/JUDGE_GRAY.md", VARIANTS, cases, driver, _standin, _score,
        check_case=lambda: (examples[0].id, {"ex": examples[0]}),
    )
