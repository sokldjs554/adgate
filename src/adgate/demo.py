"""통합 데모: 광고주 한 사람의 흐름을 모듈 경계를 넘어 끝까지 돌린다(docs/DEMO.md).

    ① 셋업 대화(campaign) → ② 소재 생성(creative: 정화·사실 인용·게이트) → ③ 심사(review: 반려 사유 → 수정안 → 재심사)
    → ④ 성과 이상 탐지·진단·액션·리포트(insight) → ⑤ 진단이 가리킨 액션(반려 소재 수정)을 ③ 의 수정안으로 닫는다.

이것은 **데모이지 측정이 아니다.** 각 모듈의 수치는 EVAL_*.md 의 사전등록 평가가 낸다. 여기서는 *모듈 사이의 이음새*(한 모듈의 출력이 다음 모듈의 입력이 되는 곳)에서
무엇을 검사하는지가 전사(transcript)와 `checks` 로 남는다. 모델이 들어갈 자리는 모드로 정한다:

* ``offline`` — 모델 없이 결정론: 광고주 발화의 추출은 *정답을 그대로 돌려주는 오라클*(모델이 아니다), 소재 생성은 정답 사실을 이은 대역(모델이 아니다), 리포트는 템플릿.
  오라클·대역은 *파이프라인의 게이트가 같은 입력에 같은 판정을 내는지*를 보이는 용도다. 이 모드에서 보이는 문장은 모델이 쓴 것이 아니다.
* ``recorded`` — 실호출 녹음(`data/llm/<suite>/<변형>.jsonl`)을 재생한다: 같은 에이전트·같은 게이트에 *실제 모델이 낸 응답*이 들어간다. 녹음이 없으면 그 단계는 '녹음 없음'이다(조용히 대체하지 않는다).

성과 단계의 시나리오는 데모용 한 건이고, 같은 구조의 시드 몇 개를 모두 돌려 결과를 함께 남긴다(`seed_sweep` — 잘 맞은 시드만 고르지 않는다).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .campaign.agent import Agent, Session
from .campaign.evaluate import oracle_client
from .campaign.scenarios import Scenario
from .campaign.schema import parse_date
from .campaign.suite import load_split
from .config import ReviewConfig
from .creative.evaluate import candidate_blob, candidate_fields, fabricated_numbers, leaks, source_text
from .creative.page import parse_page
from .creative.pipeline import CreativeConfig, run_product
from .creative.products import LoadedProduct
from .creative.suite import load_all as load_products
from .creative.suite import standin_client
from .insight.analysis import Observation, detect, diagnose, recommend
from .insight.evaluate import run_naive
from .insight.report import ACTION_KO, CAUSE_KO, build_facts, template_report, verify_report
from .insight.sim import CampaignParams, Event, Incident, IncidentScenario, gold, simulate
from .llm import LLMClient, LLMMissing, RecordedClient
from .models import Advertiser, Creative
from .pipeline import review
from .policy import load_policy
from .rewrite import rewrite

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO = "S-B05"       # 건강기능식품, 상담 신청, 일 5만 원, 발화에 주입 시도가 섞여 있다
DEFAULT_PRODUCT = "P-A05"        # 같은 업종(건강기능식품), 페이지에 위반 주장과 숨은 지시가 있다
OBJECTIVE_FOR_SIM = {"awareness": "traffic", "traffic": "traffic", "conversion": "conversion", "app_install": "app_install", "lead": "lead"}
SEEDS = (11, 12, 13, 14, 15)
# 광고주가 직접 쓴 초안(검증 전). 첫째는 자동으로 고칠 수 있는 위반, 둘째는 사람이 고쳐야 하는 위반(질병 표방)이다.
DRAFTS = (
    {"headline": "비움당 슬림 다이어트 영양제", "body": "국내 1위 체지방 감량 영양제, 3주 만에 -5kg 보장합니다", "cta": "구매하기"},
    {"headline": "당뇨 예방에 도움이 되는 영양제", "body": "혈당 걱정 끝, 꾸준히 드세요", "cta": "구매하기"},
)


def _client(mode: str, suite: str, variant: str | None, offline: LLMClient | None, root: Path) -> tuple[LLMClient | None, str]:
    """단계가 쓸 클라이언트와 그 출처 표기. recorded 인데 녹음 파일이 없으면 (None, '녹음 없음')."""
    if mode == "offline":
        return offline, "offline 대역(모델 아님)"
    path = root / "data" / "llm" / suite / f"{variant}.jsonl"
    if not path.exists():
        return None, f"녹음 없음({suite}/{variant})"
    return RecordedClient.from_file(path), f"녹음 재생({suite}/{variant}, 실제 모델 응답)"


# --------------------------------------------------------------------------- ① 셋업 대화
def step_setup(sc: Scenario, client: LLMClient | None, source: str) -> dict[str, Any]:
    out: dict[str, Any] = {"scenario": sc.id, "source": source, "persona": sc.persona, "traps": sc.traps, "turns": []}
    if client is None:
        out["error"] = source
        return out
    session = Session(today=parse_date(sc.today), verified=sc.verified)
    agent = Agent(client)
    try:
        for turn in sc.turns:
            r = agent.step(session, turn.user)
            out["turns"].append({
                "user": r.utterance, "intent": r.intent, "status": r.status, "assistant": r.message,
                "accepted": [{"path": a["path"], "value": a["value"], "quote": a["quote"], "derived": a["derived"]} for a in r.accepted],
                "rejected": [{"path": x.path, "quote": x.quote, "reason": x.reason} for x in r.rejected],
                "issues": [f"{i.severity}:{i.code}" for i in r.issues],
            })
    except LLMMissing as exc:
        out["error"] = f"녹음에 없는 호출: {exc}"
        return out
    out["final_status"] = out["turns"][-1]["status"]
    out["spec"] = {p: {"value": v.value, "source": v.source, "quote": v.quote} for p, v in session.slots.items()}
    out["assumed_defaults"] = {p: session.effective_spec()[p] for p in session.assumed_paths()}
    return out


# --------------------------------------------------------------------------- ② 소재 생성
def step_creative(lp: LoadedProduct, spec_category: str | None, client: LLMClient | None, source: str) -> dict[str, Any]:
    p = lp.product
    out: dict[str, Any] = {
        "product": p.id, "source": source, "advertiser": p.advertiser.model_dump(), "page_traps": p.gold.traps,
        "category_matches_conversation": spec_category == p.advertiser.category, "conversation_category": spec_category,
        "has_landing_claims": bool(p.gold.unsafe_claims),
    }
    if client is None:
        out["error"] = source
        return out
    try:
        res = run_product(lp, client, load_policy(), CreativeConfig())
    except LLMMissing as exc:
        out["error"] = f"녹음에 없는 호출: {exc}"
        return out
    if res.status == "failed" and res.error == "LLMMissing":      # run_product 는 호출 실패를 상품의 실패로 접는다 — 녹음에 없는 호출은 데모에서 *빈틈*으로 드러낸다
        out["error"] = "녹음에 없는 호출(LLMMissing)"
        return out
    doc = parse_page(lp.html)
    src = source_text(lp)
    cands = []
    for i in res.recommended:
        c = res.traces[i].attempts[-1].candidate
        blob = candidate_blob(c, lp)
        lk = leaks(blob, p.gold, candidate_fields(c, lp))
        cands.append({
            "trace": i, "headline": c.headline, "body": c.body, "cta": c.cta, "disclosure": c.disclosure,
            "gold_leaks": {k: v for k, v in lk.items() if v}, "fabricated_numbers": fabricated_numbers(blob, src),
            "hidden_text_echoed": [t for t in doc.dropped if len(t) >= 8 and t in blob],
        })
    out.update({
        "status": res.status, "error": res.error, "hidden_dropped": res.hidden_dropped, "injection_flagged": res.injection_flagged,
        "facts": len(res.facts), "ungrounded_facts": res.ungrounded_facts, "screened_out": res.screened_out, "calls": res.calls,
        "attempts": [{"candidate": n, "final": t.final_status, "attempts": [{"outcome": a.outcome, "problems": [f"{x.kind}:{x.code}" for x in a.problems]} for a in t.attempts]}
                     for n, t in enumerate(res.traces)],
        "recommended": cands, "image_texts": res.image_texts,
    })
    return out


# --------------------------------------------------------------------------- ③ 심사 (광고주가 쓴 초안)
def _reasons(r: Any) -> list[dict[str, Any]]:
    return [{"rule": x.rule_id, "severity": x.severity, "field": x.field, "quote": x.quote, "message": x.message, "fix": x.fix} for x in r.reasons]


def step_review(advertiser: Advertiser, landing_text: str | None) -> dict[str, Any]:
    """초안마다 ① 광고 문구만 심사 → 수정안 → 재심사, ② 같은 광고를 실제 랜딩 글과 함께 다시 심사(랜딩이 스스로 위반 주장을 하면 문구를 고쳐도 사람이 본다)."""
    cfg = ReviewConfig()
    rows = []
    for n, draft in enumerate(DRAFTS):
        c = Creative(ad_id=f"demo-draft-{n}", advertiser=advertiser, **draft)
        r = review(c, cfg)
        rw = rewrite(c, r, cfg)
        final_ad = rw.candidate if rw.candidate is not None else c
        lr = review(final_ad.model_copy(update={"landing_text": landing_text}), cfg)
        rows.append({
            "draft": draft, "outcome": r.outcome, "reasons": _reasons(r),
            "rewrite_status": rw.status, "applied": [a.model_dump() for a in rw.applied], "unresolved": rw.unresolved,
            "rewritten": ({"headline": rw.candidate.headline, "body": rw.candidate.body, "disclosure": rw.candidate.disclosure} if rw.candidate else None),
            "outcome_after": rw.verification.outcome_after if rw.verification else None, "retention": rw.verification.retention if rw.verification else None,
            "with_landing": {"outcome": lr.outcome, "ad_is_rewritten": rw.candidate is not None, "reasons": _reasons(lr), "landing_findings": [x.quote for x in lr.reasons if x.field == "landing_text"]},
        })
    return {"drafts": rows}


# --------------------------------------------------------------------------- ④ 성과 리포트
def _sim_scenario(seed: int, objective: str, daily_budget: int) -> IncidentScenario:
    return IncidentScenario(
        id=f"demo-{seed}", seed=seed, days=35, campaign=CampaignParams(objective=OBJECTIVE_FOR_SIM.get(objective, "traffic"), daily_budget_krw=daily_budget, n_creatives=3),
        incidents=[Incident(cause="ad_rejected", start_day=18, magnitude=1.0, creative=1)],
        distractor_events=[Event(day=9, type="campaign_edit", detail="캠페인 이름 변경"), Event(day=24, type="creative_added", detail="소재 추가(심사 대기)")],
        note="데모: 소재 하나가 18일차에 반려된다",
    )


def _one_seed(seed: int, objective: str, daily_budget: int, *, full: bool) -> dict[str, Any]:
    sc = _sim_scenario(seed, objective, daily_budget)
    sim = simulate(sc)
    g = gold(sim)
    obs = Observation(sim.days, sim.events)
    det = detect(obs)
    row: dict[str, Any] = {"seed": seed, "gold_episodes": g["episodes"], "gold_causes": g["causes"], "alerts": len(det.episodes), "episodes": []}
    for ep in det.episodes:
        dx = diagnose(obs, det, ep)
        recs = recommend(obs, dx)
        e: dict[str, Any] = {"start": ep.start, "end": ep.end, "alert_day": ep.alert_day, "cause": dx.top, "explained": dx.explained, "actions": [r["action"] for r in recs]}
        if full:
            facts = build_facts(obs, det, ep, dx, recs)
            rep = template_report(facts, dx, recs)
            e.update({"facts": [{"id": f.id, "kind": f.kind, "text": f.text} for f in facts], "report": rep, "report_problems": verify_report(rep, facts, dx, recs),
                      "cause_ko": CAUSE_KO.get(dx.top or "", None), "actions_ko": [ACTION_KO[r["action"]] for r in recs], "avoid_ko": [ACTION_KO[a] for a in (recs[0].get("avoid", []) if recs else [])]})
        row["episodes"].append(e)
    caught = [e for e in row["episodes"] if e["cause"] == "ad_rejected"]
    row["caught"] = bool(caught)
    row["acted"] = bool(caught) and "fix_rejected_ad" in caught[0]["actions"]
    if full:
        row["naive"] = [{"start": a.start, "end": a.end, "cause": a.cause, "actions": a.actions} for a in run_naive(sim)]
    return row


def step_report(objective: str, daily_budget: int) -> dict[str, Any]:
    sweep = [_one_seed(s, objective, daily_budget, full=(s == SEEDS[0])) for s in SEEDS]
    return {"incident": "ad_rejected(소재 1, 18일차)", "campaign": {"objective": OBJECTIVE_FOR_SIM.get(objective, "traffic"), "daily_budget_krw": daily_budget},
            "shown_seed": SEEDS[0], "shown": sweep[0], "seed_sweep": [{k: r[k] for k in ("seed", "gold_episodes", "alerts", "caught", "acted")} for r in sweep]}


# --------------------------------------------------------------------------- 이음새 검사
def _checks(setup: dict[str, Any], creative: dict[str, Any], rev: dict[str, Any], rep: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if "error" not in setup:
        turns = setup["turns"]
        utter = {i: t["user"] for i, t in enumerate(turns)}
        add("셋업: 광고주·파생 값마다 발화에서 글자 그대로 복사한 인용이 있다",
            all(a["quote"] and any(a["quote"] in utter[i] for i in utter) for t in turns for a in t["accepted"]))
        add("셋업: ready 는 요약을 보인 뒤 광고주가 확인한 바로 다음 턴에만 나온다",
            setup["final_status"] != "ready" or (len(turns) >= 2 and turns[-2]["status"] == "awaiting_confirm"))
        add("셋업: 사양의 값은 전부 광고주가 말했거나(user) 말에서 계산한(derived) 것이다 — 코드가 가정한 값은 따로 표시된다", all(v["source"] in ("user", "derived") for v in setup["spec"].values()) and "assumed_defaults" in setup)
        if "injection" in setup["traps"]:
            add("셋업: 발화에 섞인 시스템 지시 문장의 값은 상태에 들어가지 않았다", any(r["reason"] for t in turns for r in t["rejected"]) or not any(
                a["path"] == "daily_budget_krw" and a["value"] >= 100_000_000 for t in turns for a in t["accepted"]))
    if "error" not in creative:
        add("소재: 후보 글에 상품 페이지의 숨은 지시 문장이 그대로 나오지 않는다", all(not c["hidden_text_echoed"] for c in creative["recommended"]))
        add("소재: 후보의 숫자는 모두 페이지·이미지에 있다(지어낸 숫자 없음)", all(not c["fabricated_numbers"] for c in creative["recommended"]))
        add("소재: 추천된 후보가 상품 작성자가 심어 둔 위반 주장·주입 지시·부풀림 문구를 하나도 담지 않았다(정답 대조)", all(not c["gold_leaks"] for c in creative["recommended"]))
        add("소재: 광고주가 말한 업종과 상품 광고주의 업종이 같다", creative["category_matches_conversation"])
    first, second = rev["drafts"][0], rev["drafts"][1]
    add("심사: 광고 문구만 보면 위반 초안은 반려되고 모든 사유에 원문 인용이 있다", first["outcome"] == "REJECT" and all(r["quote"] for r in first["reasons"]))
    add("심사: 자동으로 고칠 수 있는 위반은 수정안이 재심사 APPROVE 와 수치 보존을 통과해야 '검증됨'이다", first["rewrite_status"] == "verified" and first["outcome_after"] == "APPROVE")
    add("심사: 자동으로 고칠 수 없는 위반(질병 표방)은 고치지 않고 사람에게 넘긴다", second["outcome"] == "REJECT" and second["rewrite_status"] == "unavailable" and bool(second["unresolved"]))
    if creative.get("has_landing_claims"):
        add("심사: 광고 문구를 고쳐도 랜딩 페이지 자체가 위반 주장을 하면 자동 통과하지 않고 사람에게 간다",
            first["with_landing"]["outcome"] != "APPROVE" and bool(first["with_landing"]["landing_findings"]))
    shown = rep["shown"]
    add("성과: 리포트의 모든 문단·권고는 사실에 묶여 있고 검증을 통과한다(숫자 근거·금지 액션 없음)", all(not e["report_problems"] for e in shown["episodes"]))
    add("성과: 시드를 골라 맞춘 것이 아니다 — 모든 시드의 결과가 전사에 남는다", len(rep["seed_sweep"]) == len(SEEDS))
    return checks


# --------------------------------------------------------------------------- 실행
def run_demo(
    mode: str = "offline", *, variant: str | None = None, scenario_id: str = DEFAULT_SCENARIO, product_id: str = DEFAULT_PRODUCT, root: Path | None = None,
) -> dict[str, Any]:
    if mode not in ("offline", "recorded"):
        raise ValueError(f"알 수 없는 모드: {mode}")
    if mode == "recorded" and not variant:
        raise ValueError("recorded 모드는 변형(--variant)이 필요하다")
    dev, test = load_split()
    sc = next((s for s in [*dev, *test] if s.id == scenario_id), None)
    if sc is None:
        raise ValueError(f"알 수 없는 시나리오: {scenario_id}")
    d, t = load_products()
    lp = next((x for x in [*d, *t] if x.product.id == product_id), None)
    if lp is None:
        raise ValueError(f"알 수 없는 상품: {product_id}")

    root = root or ROOT
    c_client, c_src = _client(mode, "campaign", variant, oracle_client(sc), root)
    setup = step_setup(sc, c_client, c_src)
    g_client, g_src = _client(mode, "creative", variant, standin_client({"lp": lp}), root)
    spec_category = (setup.get("spec", {}).get("ad_category") or {}).get("value")
    creative = step_creative(lp, spec_category, g_client, g_src)
    landing = "\n".join(t for _, t in parse_page(lp.html).lines())[:4000]
    rev = step_review(lp.product.advertiser, landing)
    spec = setup.get("spec", {})
    budget = int((spec.get("daily_budget_krw") or {}).get("value") or 50_000)
    objective = str((spec.get("objective") or {}).get("value") or "traffic")
    rep = step_report(objective, budget)
    loop: dict[str, Any] = {"diagnosed_action": None}
    ep = next((e for e in rep["shown"]["episodes"] if e["cause"] == "ad_rejected"), None)
    if ep is not None and "fix_rejected_ad" in ep["actions"]:
        first = rev["drafts"][0]
        loop = {
            "diagnosed_action": "fix_rejected_ad", "rewrite_status": first["rewrite_status"], "ad_text_outcome_after": first["outcome_after"],
            "outcome_with_landing": first["with_landing"]["outcome"], "landing_findings": first["with_landing"]["landing_findings"],
            "note": "성과 진단이 가리킨 '반려 소재 수정'을 ③ 의 수정안(재심사 통과)으로 잇는다. 광고 문구는 고쳐 통과하지만, 랜딩 페이지가 스스로 위반 주장을 하면 자동으로 다시 내보내지 않고 광고주·사람에게 넘긴다.",
        }
    checks = _checks(setup, creative, rev, rep)
    errors = {name: step["error"] for name, step in (("setup", setup), ("creative", creative)) if step.get("error")}
    return {
        "meta": {"mode": mode, "variant": variant, "scenario": scenario_id, "product": product_id,
                 "note": "데모이지 측정이 아니다. 모듈별 수치는 docs/EVAL_*.md. offline 모드의 문장은 모델이 쓴 것이 아니다."},
        "setup": setup, "creative": creative, "review": rev, "report": rep, "loop": loop, "checks": checks, "errors": errors,
        "all_ok": all(c["ok"] for c in checks) and not errors,
    }


def dump(res: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- 사람이 읽는 전사
def transcript(res: dict[str, Any]) -> str:
    m = res["meta"]
    out = [f"# 통합 데모 — 모드 {m['mode']}{('/' + m['variant']) if m['variant'] else ''}", "", f"> {m['note']}", ""]
    s = res["setup"]
    out += [f"## ① 셋업 대화 ({s['scenario']}, {s['source']})", f"광고주: {s.get('persona', '')}", ""]
    if "error" in s:
        out += [f"**{s['error']}**", ""]
    for i, t in enumerate(s["turns"], start=1):
        out += [f"**광고주 {i}.** {t['user']}", ""]
        for a in t["accepted"]:
            out.append(f"- 받은 값 `{a['path']}` = `{a['value']}` ({'계산' if a['derived'] else '발화'} · 인용 “{a['quote']}”)")
        for r in t["rejected"]:
            out.append(f"- 버린 값 `{r['path']}` — {r['reason']} (인용 “{r['quote']}”)")
        out += ["", f"**어시스턴트 [{t['status']}].** {t['assistant']}", ""]
    if "spec" in s:
        out += ["최종 사양(광고주가 말한·계산한 값):", "", "| 슬롯 | 값 | 출처 | 인용 |", "|---|---|---|---|"]
        out += [f"| `{p}` | `{v['value']}` | {v['source']} | {v['quote']} |" for p, v in s["spec"].items()]
        out += ["", f"비워 둬서 코드가 *가정*한 값(요약에서 확인을 받는다): `{s['assumed_defaults']}`", ""]
    c = res["creative"]
    out += [f"## ② 소재 생성 ({c['product']}, {c['source']})", f"광고주 {c['advertiser']['name']}({c['advertiser']['category']}) · 페이지 함정 {c['page_traps']} · 대화의 업종과 일치: {c['category_matches_conversation']}", ""]
    if "error" in c and c["error"]:
        out += [f"**{c['error']}**", ""]
    else:
        out += [f"상태 `{c['status']}` · 숨은 글 {c['hidden_dropped']}조각 제거 · 지시 문장 {len(c['injection_flagged'])}개 정화 · 사실 {c['facts']}개(인용 못 찾아 버린 것 {c['ungrounded_facts']}) · 사실 심사에서 걸러진 것 {len(c['screened_out'])}개 · 호출 {c['calls']}", ""]
        for a in c["attempts"]:
            out.append(f"- 후보 {a['candidate']}: " + " → ".join(f"{x['outcome']}{('(' + ', '.join(x['problems']) + ')') if x['problems'] else ''}" for x in a["attempts"]) + f" ⇒ {a['final']}")
        out += [""]
        for k, cand in enumerate(c["recommended"], start=1):
            out += [f"**추천 {k}.** {cand['headline']} / {cand['body']} [{cand['cta']}]" + (f" · 고지: {cand['disclosure']}" if cand["disclosure"] else ""), ""]
    out += ["## ③ 심사 — 광고주가 직접 쓴 초안", ""]
    for n, d in enumerate(res["review"]["drafts"], start=1):
        out += [f"**초안 {n}.** {d['draft']['headline']} / {d['draft']['body']} ⇒ **{d['outcome']}** (광고 문구만)", ""]
        out += [f"- [{r['severity']}] {r['rule']} {r['field']}: “{r['quote']}” — {r['message']} (수정: {r['fix']})" for r in d["reasons"]]
        if d["rewritten"]:
            out += ["", f"수정안({d['rewrite_status']}, 재심사 {d['outcome_after']}, 원문 보존 {d['retention']}): {d['rewritten']['headline']} / {d['rewritten']['body']}"]
        else:
            out += ["", f"수정안 없음({d['rewrite_status']}) — 자동으로 고칠 수 없는 위반이라 광고주 입력이 필요하다: {d['unresolved']}"]
        wl = d["with_landing"]
        out += [f"같은 광고를 상품 페이지 글과 함께 다시 심사: **{wl['outcome']}**" + (f" — 페이지 자체의 위반 주장: {wl['landing_findings']}" if wl["landing_findings"] else ""), ""]
    r = res["report"]
    sh = r["shown"]
    out += [f"## ④ 성과 리포트 — {r['incident']}, 시드 {r['shown_seed']}", f"캠페인 {r['campaign']}", ""]
    for e in sh["episodes"]:
        out += [f"**경보** {e['alert_day']}일차(변화 {e['start']}~{e['end']}일차) → 원인 {e.get('cause_ko')} → 권고 {e.get('actions_ko')} · 하지 말 것 {e.get('avoid_ko')}", ""]
        if e.get("report"):
            out += [f"### {e['report']['headline']}", ""] + [p["text"] for p in e["report"]["paragraphs"]] + [""]
            out += [f"- 다음 할 일: {s2['action']} — {s2['rationale']}" for s2 in e["report"]["next_steps"]] + [""]
        out += [f"리포트 검증에서 걸린 것: {e['report_problems']}", ""]
    out += [f"비교 — 일반 대시보드 규칙(naive)이 같은 데이터에서 낸 경보: {sh['naive']}", "", "시드별 결과(고르지 않았다):", ""]
    out += ["| 시드 | 정답 구간 | 경보 | 소재 반려로 진단 | 반려 소재 수정을 권고 |", "|---|---|---|---|---|"]
    out += [f"| {x['seed']} | {x['gold_episodes']} | {x['alerts']} | {x['caught']} | {x['acted']} |" for x in r["seed_sweep"]]
    out += ["", "## ⑤ 고리 닫기", "", json.dumps(res["loop"], ensure_ascii=False), "", "## 이음새 검사", ""]
    out += [f"- [{'x' if c2['ok'] else ' '}] {c2['name']}" for c2 in res["checks"]]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- 문서(docs/DEMO.md) — 산출물에서만 만든다
ARTIFACTS = (("demo_offline.json", "offline — 모델 없이 결정론(오라클·대역·템플릿)"), ("demo_recorded_haiku.json", "recorded/haiku — 실제 모델(haiku)의 녹음을 재생"),
             ("demo_recorded_sonnet.json", "recorded/sonnet — 실제 모델(sonnet)의 녹음을 재생"))
SEAMS = (
    ("① 셋업 → ②·④", "광고주의 말 → 캠페인 사양(업종·목표·예산·랜딩 …)", "값마다 *발화에서 글자 그대로 복사한 인용*, 주입 문장의 값은 버림, 확인 뒤에야 ready, 가정값은 따로 표시", "[EVAL_SETUP.md](EVAL_SETUP.md)"),
    ("② 소재 생성", "상품 페이지·이미지 → 광고 후보", "숨은 글 제거, 지시 문장 정화, 사실은 원문 인용이 있어야 사실, 후보의 숫자는 원문에 있어야 함, 정책 게이트 + 수리", "[EVAL_CREATIVE.md](EVAL_CREATIVE.md)"),
    ("③ 심사", "광고 문구(+랜딩 글) → 판정·사유·수정안", "모든 사유에 원문 인용, 수정안은 재심사 APPROVE·수치 보존을 통과해야 '검증됨', 고칠 수 없는 위반은 사람에게, 랜딩이 위반이면 문구를 고쳐도 사람에게", "[RESULTS.md](RESULTS.md) · [EVAL_PLAN.md](EVAL_PLAN.md)"),
    ("④ 성과 리포트", "일별 지표 → 이상 탐지·원인·액션·문장", "같은 요일 기준선·이틀 연속 조건, 변경 이력은 단서일 뿐, 리포트의 모든 문단은 사실에 묶이고 숫자 근거·금지 액션 검사를 통과", "[EVAL_INSIGHT.md](EVAL_INSIGHT.md)"),
    ("⑤ 고리 닫기", "진단된 액션(반려 소재 수정) → ③ 의 수정안", "광고 문구를 고쳐도 랜딩 페이지가 스스로 위반 주장을 하면 자동으로 내보내지 않는다", "—"),
)


def markdown(root: Path) -> str | None:
    """``artifacts/demo_*.json`` 에서 docs/DEMO.md 를 만든다. 오프라인 산출물이 없으면 None."""
    loaded = []
    for name, label in ARTIFACTS:
        path = root / "artifacts" / name
        if path.exists():
            loaded.append((label, json.loads(path.read_text(encoding="utf-8"))))
    if not loaded:
        return None
    out = [
        "# 통합 데모 (자동 생성)", "",
        "> 이 파일은 `adgate docs` 가 `artifacts/demo_*.json` 에서 만든다. **손으로 고치지 않는다.** 산출물은 `adgate demo --out artifacts/demo_offline.json`,",
        "> `adgate demo --mode recorded --variant haiku --out artifacts/demo_recorded_haiku.json` 처럼 만든다.", "",
        "**이것은 데모이지 측정이 아니다.** 각 모듈의 수치는 사전등록 평가(`EVAL_*.md`)가 낸다. 여기서는 모듈 *사이의 이음새* — 한 모듈의 출력이 다음 모듈의 입력이 되는 곳 — 에서 무엇을 검사하는지가 "
        "전사(transcript)와 이음새 검사로 남는다. 광고주 한 사람의 흐름은 이렇다: 대화로 캠페인을 만들고 → 상품 페이지에서 소재를 만들고 → 광고주가 쓴 문구를 심사하고 → 집행 중 소재 하나가 반려돼 성과가 꺾이면 "
        "원인을 진단해 '반려 소재 수정'을 권하고 → 그 수정을 심사의 수정안으로 잇는다.", "",
        "모드: **offline** 은 모델 없이 결정론이다 — 광고주 발화의 추출은 *정답을 그대로 돌려주는 오라클*, 소재 생성은 정답 사실을 이은 대역이라 그 문장은 모델이 쓴 것이 아니다(게이트가 같은 입력에 같은 판정을 내는지 보이는 용도). "
        "**recorded** 는 실호출 녹음을 재생한다 — 같은 에이전트·같은 게이트 안에 *실제 모델이 낸 응답*이 들어간다. 녹음이 없는 단계는 '녹음 없음'으로 드러내고 조용히 대체하지 않는다.", "",
        "## 이음새에서 검사하는 것", "", "| 경계 | 넘어가는 것 | 그 자리의 검사 | 평가 |", "|---|---|---|---|",
    ]
    out += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in SEAMS]
    out += ["", "성과 단계의 사건·시드는 데모용이고(시드 5개를 모두 전사에 남긴다 — 잘 맞은 것만 고르지 않았다) 탐지·진단의 *성능*은 [EVAL_INSIGHT.md](EVAL_INSIGHT.md) 의 독립 시나리오 60건이 잰다. "
            "일반 대시보드 규칙(naive)이 같은 데이터에서 다른 원인을 가리키는 비교도 전사에 있다.", ""]
    for label, res in loaded:
        ok = sum(c["ok"] for c in res["checks"])
        out += [f"## {label}", "", f"이음새 검사 **{ok}/{len(res['checks'])} 통과**" + (f", 돌리지 못한 단계: {res['errors']}" if res["errors"] else "") + ".", ""]
        out += [f"- [{'x' if c['ok'] else ' '}] {c['name']}" for c in res["checks"]]
        out += ["", "<details><summary>전사 펼치기</summary>", "", transcript(res).replace("# 통합 데모", "### 전사", 1), "", "</details>", ""]
    return "\n".join(out)
