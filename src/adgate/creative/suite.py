"""소재 생성 측정 묶음(docs/EVAL_CREATIVE.md). 사례 하나 = 상품 하나, 사례 안에서 팔 여럿을 같은 클라이언트로 돌린다(같은 호출은 녹음에서 재생된다).

* ``creative``(sonnet): full · nogate · nosanitize · noscreen · direct + 쌍대 품질 비교(full vs direct). ``creative``(haiku): full 만.
* 오프라인 팔(template: LLM 없음, 정답 사실을 이어 붙임)은 ``offline`` 이 돌린다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..eval.metrics import ratio
from ..llm import CaseOutcome, LLMClient, LLMError, LLMMissing, LLMTask, ScriptedClient
from ..llm.core import Part
from ..policy import Policy, load_policy
from ..suites import Driver, Suite, Variant, register
from ..vision import texts as spec_texts
from .baseline import template_candidates
from .evaluate import (
    ArmOutput,
    aggregate,
    candidate_blob,
    candidate_fields,
    constraints_for,
    fabricated_numbers,
    from_result,
    fuzzy_unsafe,
    independent_review,
    leaks,
    score_arm,
    source_text,
)
from .models import Candidate, Fact
from .page import parse_page
from .pipeline import CreativeConfig, account_block, run_product
from .products import LoadedProduct, load_products
from .tasks import COMPARE, compare_user, generate_task, generate_user

ROOT = Path(__file__).resolve().parents[3] / "data" / "product"
VARIANTS = {"sonnet": Variant("sonnet", "claude-sonnet-5-5", "전 팔 + 쌍대 비교"), "haiku": Variant("haiku", "claude-haiku-5-5", "full 만")}
ARMS_FULL = ("full", "nogate", "nosanitize", "noscreen", "direct", "compare")
CFG = {
    "full": CreativeConfig(), "nogate": CreativeConfig(gate=False), "nosanitize": CreativeConfig(sanitize=False), "noscreen": CreativeConfig(screen=False),
}


def load_all(root: Path | None = None) -> tuple[list[LoadedProduct], list[LoadedProduct]]:
    base = root or ROOT
    return load_products(base / "dev.jsonl", base / "pages"), load_products(base / "test.jsonl", base / "pages")


def gold_facts(lp: LoadedProduct) -> list[Fact]:
    return [Fact(id=g.id, text=g.text, source=g.source, kind="other", importance=g.priority) for g in lp.product.gold.facts]


def arm_direct(lp: LoadedProduct, client: LLMClient, policy: Policy) -> ArmOutput:
    """정화·사실 추출·사실 심사·게이트 없이, 페이지의 보이는 모든 줄을 '사실'로 삼아 정책을 아는 생성 프롬프트에 그대로 넣는다."""
    cons = constraints_for(lp)
    lines = [t for _, t in parse_page(lp.html).lines()]
    facts = [Fact(id=f"f{i + 1}", text=t[:120], source="page", kind="other", importance=2) for i, t in enumerate(lines[:14]) if len(t.strip()) >= 2]
    try:
        raw = client.call(generate_task(policy, cons), generate_user(lp.product.advertiser, lp.product.brand_voice, facts, cons)).response
    except LLMMissing:
        raise
    except LLMError as exc:
        return ArmOutput("failed", [], type(exc).__name__, 1)
    cands: list[Candidate] = []
    for item in raw.get("candidates", [])[: cons.n_candidates]:
        try:
            cands.append(Candidate.model_validate(item))
        except ValidationError:
            continue
    return ArmOutput("ok" if cands else "no_safe_candidate", cands, None, 1, 0, facts)


def arm_template(lp: LoadedProduct) -> ArmOutput:
    cands = template_candidates(lp.product.advertiser, lp.product.brand_voice, gold_facts(lp), constraints_for(lp))
    return ArmOutput("ok" if cands else "no_safe_candidate", cands)


def _winner(r: dict[str, Any]) -> str:
    return str(r.get("winner", "tie"))


def pairwise(lp: LoadedProduct, client: LLMClient, a: ArmOutput, b: ArmOutput, facts: list[Fact]) -> dict[str, Any] | None:
    """a(파이프라인) 1순위 vs b(직접) 1순위. 순서를 바꿔 두 번 묻는다."""
    if not a.candidates or not b.candidates or not facts:
        return None
    ca, cb = a.candidates[0].model_dump(mode="json"), b.candidates[0].model_dump(mode="json")
    v = lp.product.brand_voice
    try:
        r1 = client.call(COMPARE, compare_user(v, facts, ca, cb)).response
        r2 = client.call(COMPARE, compare_user(v, facts, cb, ca)).response
    except LLMMissing:
        raise
    except LLMError as exc:
        return {"error": type(exc).__name__}
    pts = (_winner(r1) == "A") - (_winner(r1) == "B") + (_winner(r2) == "B") - (_winner(r2) == "A")
    return {"points": pts, "unsupported_a": len(r1.get("unsupported_in_a", [])) + len(r2.get("unsupported_in_b", [])),
            "unsupported_b": len(r1.get("unsupported_in_b", [])) + len(r2.get("unsupported_in_a", []))}


def run_case(lp: LoadedProduct, client: LLMClient, arms: tuple[str, ...], policy: Policy) -> dict[str, Any]:
    out: dict[str, Any] = {}
    full_res = None
    for name in ("full", "nogate", "nosanitize", "noscreen"):
        if name in arms:
            res = run_product(lp, client, policy, CFG[name])
            out[name] = from_result(res)
            if name == "full":
                full_res = res
    if "direct" in arms:
        out["direct"] = arm_direct(lp, client, policy)
    if "compare" in arms and full_res is not None and "direct" in out:
        out["compare"] = pairwise(lp, client, out["full"], out["direct"], list(full_res.facts))
    return out


def _standin(payload: dict[str, Any]) -> ScriptedClient:
    lp: LoadedProduct = payload["lp"]
    cons = constraints_for(lp)
    cands = [c.model_dump(mode="json") for c in template_candidates(lp.product.advertiser, lp.product.brand_voice, gold_facts(lp), cons)] or [{}]

    def handler(task: LLMTask, parts: list[Part]) -> dict[str, Any]:
        if task.name == "vision.read":
            return {"texts": [{"text": t, "legible": True} for t in spec_texts(lp.product.image)], "flags": [], "description": "상품 이미지"}
        if task.name == "creative.facts":
            return {"facts": [{"text": g.text, "source": g.source, "kind": "other", "importance": g.priority} for g in lp.product.gold.facts], "risky_claims": []}
        if task.name == "creative.generate":
            return {"candidates": cands}
        if task.name == "creative.repair":
            return cands[0]
        if task.name == "creative.compare":
            return {"winner": "tie", "unsupported_in_a": [], "unsupported_in_b": [], "reason": "-"}
        raise LLMError(task.name)

    return ScriptedClient(handler)


standin_client = _standin    # 공개 이름: 통합 데모(adgate demo)가 모델 없이 같은 파이프라인을 돌릴 때 쓴다


def _score(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
    dev, test = load_all()
    policy = load_policy()
    by_id = {lp.product.id: lp for lp in [*dev, *test]}
    res: dict[str, Any] = {"note": "dev(조각 A·B)는 설계자가 보았다 — 헤드라인은 test 의 첫 접촉이다."}
    for split, items in (("dev", dev), ("test", test)):
        ids = {lp.product.id for lp in items}
        sel = [o for o in outcomes if o.case in ids]
        ok = [o for o in sel if o.result is not None]
        block: dict[str, Any] = {"cases": len(sel), "failed": [o.case for o in sel if o.result is None], "arms": {}}
        for arm in ("full", "nogate", "nosanitize", "noscreen", "direct"):
            rows = [score_arm(by_id[o.case], o.result[arm], policy) for o in ok if arm in o.result]
            if rows:
                block["arms"][arm] = aggregate(rows)
        cmp_rows = [o.result["compare"] for o in ok if o.result.get("compare") and "points" in o.result["compare"]]
        if cmp_rows:
            wins, losses = sum(r["points"] > 0 for r in cmp_rows), sum(r["points"] < 0 for r in cmp_rows)
            block["compare_full_vs_direct"] = {
                "pairs": len(cmp_rows), "full_wins": ratio(wins, len(cmp_rows)), "direct_wins": ratio(losses, len(cmp_rows)), "ties": len(cmp_rows) - wins - losses,
                "unsupported_full": sum(r["unsupported_a"] for r in cmp_rows), "unsupported_direct": sum(r["unsupported_b"] for r in cmp_rows),
            }
        res[split] = block
    return res


def _analyze(variant: str, outcomes: list[CaseOutcome]) -> dict[str, Any]:
    """새어 나온 후보와 후보를 만들지 못한 상품의 목록. 새어 나온 후보마다 *게이트가 무엇을 보았는지*(시도별 심사 결과·검증 문제 코드)를 붙인다."""
    dev, test = load_all()
    policy = load_policy()
    by_id = {lp.product.id: lp for lp in [*dev, *test]}
    split = {**{lp.product.id: "dev" for lp in dev}, **{lp.product.id: "test" for lp in test}}
    leaking: list[dict[str, Any]] = []
    empty: list[dict[str, Any]] = []
    for o in outcomes:
        if o.result is None:
            continue
        lp = by_id[o.case]
        gold = lp.product.gold
        source = source_text(lp)
        for arm, out in o.result.items():
            if not isinstance(out, ArmOutput):
                continue
            if not out.candidates:
                empty.append({"id": o.case, "split": split[o.case], "arm": arm, "status": out.status, "error": out.error})
            for rank, c in enumerate(out.candidates):
                blob = candidate_blob(c, lp)
                lk, fuzzy, fab = leaks(blob, gold, candidate_fields(c, lp)), fuzzy_unsafe(blob, gold), fabricated_numbers(blob, source)
                if any(lk.values()) or fuzzy or fab:
                    leaking.append({
                        "id": o.case, "split": split[o.case], "arm": arm, "rank": rank, "traps": gold.traps, "leaks": lk, "fuzzy": fuzzy, "fabricated": fab,
                        "headline": c.headline, "body": c.body, "disclosure": c.disclosure, "independent_review": independent_review(c, lp, policy), "gate_trace": out.trace,
                    })
    return {"leaking_candidates": leaking, "no_candidates": empty, "failed_cases": [o.case for o in outcomes if o.result is None]}


@register("creative")
def creative() -> Suite:
    dev, test = load_all()
    products = [*dev, *test]

    def cases(variant: str) -> list[tuple[str, Any]]:
        arms = ARMS_FULL if variant == "sonnet" else ("full",)
        return [(lp.product.id, {"lp": lp, "arms": arms}) for lp in products]

    def driver(variant: str) -> Driver:
        policy = load_policy()

        def run(payload: dict[str, Any], client: LLMClient) -> dict[str, Any]:
            return run_case(payload["lp"], client, payload["arms"], policy)

        return run

    return Suite(
        "creative", "소재 생성: 파이프라인 vs 게이트 없음·정화 없음·심사 없음·직접 생성 + 쌍대 품질 비교", "docs/EVAL_CREATIVE.md", VARIANTS, cases, driver, _standin, _score,
        offline=offline, check_case=lambda: (dev[0].product.id, {"lp": dev[0], "arms": ("full",)}), analyze=_analyze,
    )


def offline() -> dict[str, Any]:
    """LLM 없는 팔: 정답 사실을 이어 붙인 템플릿."""
    dev, test = load_all()
    policy = load_policy()
    out: dict[str, Any] = {}
    for split, items in (("dev", dev), ("test", test)):
        rows = []
        for lp in items:
            arm = arm_template(lp)
            if account_block(lp.product.advertiser):
                arm = ArmOutput("blocked_by_account", [])     # 템플릿도 차단 규칙은 지킨다고 본다(차단은 생성 앞 단계의 일)
            rows.append(score_arm(lp, arm, policy))
        out[split] = {"products": len(items), "template": aggregate(rows)}
    return out
