"""사람 실험 키트. 질문: 검토자가 시스템 제안(엔진 판정 + 발견)을 보면 (1) 빨라지는가 (2) 더 정확해지는가 (3) 틀린 제안까지 따르는 자동화 편향이 생기는가.

설계: **피험자 내 교차(crossover)**. 검토자마다 소재 절반은 제안 없이(unassisted), 나머지 절반은 제안과 함께(assisted) 판정한다. 소재 절반 A·B 를 검토자 짝마다 맞바꾸고(A 에 제안 / B 에 제안),
블록 순서도 맞바꿔 순서·학습 효과가 조건과 섞이지 않게 한다. 모든 소재는 검토자의 절반에게는 제안과 함께, 나머지 절반에게는 제안 없이 보인다.

소재(``select_items``)는 층화 3종: 엔진이 맞고 분명한 것 · 엔진이 맞고 회색지대인 것 · **엔진이 틀린 것**(자동화 편향을 재려고 일부러 넣는다). 정답은 책자에 없다.

분석(``analyze``): 검토자마다 조건별 정확도·위험 승인율·안전 손실·걸린 시간(중앙값)·엔진이 틀린 소재에서 엔진을 따른 비율을 구하고, 조건 차이(assisted − unassisted)를 **검토자 단위로** 짝지어
부트스트랩 95% 구간과 부호 뒤집기 순열 검정으로 낸다. 소재가 아니라 검토자가 표본 단위다(한 검토자의 응답은 독립이 아니다).

이 모듈이 한 일은 *준비*다: 책자 생성, 배정, 응답 분석, 그리고 알려진 효과를 넣은 시뮬레이션 참가자로 분석이 그 효과를 되찾는지·검토자 수에 따른 검출력을 보는 것. 사람 데이터는 없다.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..config import ReviewConfig
from ..eval.dataset import Example
from ..ops.shadow import cost
from ..pipeline import review

STRATA = ("clear_correct", "gray_correct", "engine_wrong")
DECISIONS = ("APPROVE", "REVIEW", "REJECT")


@dataclass(frozen=True)
class StudyItem:
    id: str
    stratum: str
    creative: dict[str, Any]
    gold: str                          # 책자에는 없다
    engine: str
    engine_rules: tuple[str, ...]
    engine_quotes: tuple[tuple[str, str, str], ...]     # (rule_id, field, quote)


def select_items(pool: Sequence[Example], *, per_stratum: int = 16, seed: int = 0, cfg: ReviewConfig | None = None) -> list[StudyItem]:
    """풀에서 층화 추출. 정답이 분쟁(``disputed``)인 소재는 뺀다(정답이 확실해야 사람의 판정을 채점할 수 있다)."""
    cfg = cfg or ReviewConfig()
    rng = random.Random(seed)
    by: dict[str, list[StudyItem]] = {s: [] for s in STRATA}
    for e in pool:
        if "disputed" in e.gold.tags:
            continue
        r = review(e.creative, cfg)
        real = [f for f in r.findings if f.severity != "info"]
        item = StudyItem(
            e.id, "", json.loads(e.creative.model_dump_json()), e.gold.decision, r.outcome, tuple(sorted({f.rule_id for f in real})),
            tuple((f.rule_id, f.field, f.quote) for f in real if f.field and f.quote),
        )
        stratum = "engine_wrong" if r.outcome != e.gold.decision else ("gray_correct" if "gray" in e.gold.tags else "clear_correct")
        by[stratum].append(StudyItem(**{**item.__dict__, "stratum": stratum}))
    out: list[StudyItem] = []
    for s in STRATA:
        bucket = sorted(by[s], key=lambda i: i.id)
        if len(bucket) < per_stratum:
            raise ValueError(f"{s} 소재가 {len(bucket)}개뿐이다(필요 {per_stratum})")
        out += rng.sample(bucket, per_stratum)
    rng.shuffle(out)
    return out


def practice_items(pool: Sequence[Example], n: int = 2, cfg: ReviewConfig | None = None) -> list[StudyItem]:
    """연습용: 엔진이 맞히고 정답이 분명한(정상 또는 명백한 반려) 소재. 기록에 포함되지 않는다."""
    cfg = cfg or ReviewConfig()
    out: list[StudyItem] = []
    for e in pool:
        if len(out) >= n:
            break
        if "disputed" in e.gold.tags or not ({"clean", "clear", "normal"} & set(e.gold.tags)):
            continue
        r = review(e.creative, cfg)
        if r.outcome != e.gold.decision:
            continue
        real = [f for f in r.findings if f.severity != "info"]
        out.append(StudyItem(e.id, "practice", json.loads(e.creative.model_dump_json()), e.gold.decision, r.outcome, tuple(sorted({f.rule_id for f in real})), tuple((f.rule_id, f.field, f.quote) for f in real if f.field and f.quote)))
    return out


@dataclass(frozen=True)
class Trial:
    reviewer: str
    n: int
    item_id: str
    condition: str          # assisted | unassisted
    block: int              # 1 | 2


def design(items: Sequence[StudyItem], reviewers: int, *, seed: int = 0) -> list[Trial]:
    """층화한 반반 분할(A·B)을 검토자 짝마다 맞바꾸고 블록 순서도 맞바꾼다."""
    rng = random.Random(seed)
    halves: tuple[list[StudyItem], list[StudyItem]] = ([], [])
    for s in STRATA:
        bucket = [i for i in items if i.stratum == s]
        rng.shuffle(bucket)
        for k, it in enumerate(bucket):
            halves[k % 2].append(it)
    trials: list[Trial] = []
    for r in range(reviewers):
        assisted_half = r % 2                         # 짝수 검토자는 A 에 제안, 홀수는 B 에 제안
        assisted_first = (r // 2) % 2 == 1            # 네 명마다 블록 순서가 한 번씩 바뀐다
        blocks = [(halves[assisted_half], "assisted"), (halves[1 - assisted_half], "unassisted")]
        if not assisted_first:
            blocks.reverse()
        n = 0
        for b, (its, cond) in enumerate(blocks, start=1):
            order = list(its)
            random.Random(f"{seed}:{r}:{b}").shuffle(order)
            for it in order:
                n += 1
                trials.append(Trial(f"R{r + 1:02d}", n, it.id, cond, b))
    return trials


# --- 책자(HTML) --------------------------------------------------------------------------------------------------------------------


def _safe_json(obj: Any) -> str:
    """<script> 안에 넣을 JSON. 소재 글에 ``</script>`` 가 있어도 태그를 닫지 못하게 <, >, &, 줄 구분자를 이스케이프한다."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>광고 심사 연구 — __RID__</title>
<style>
:root{--bg:#fafaf8;--fg:#1b1b1b;--muted:#666;--line:#d8d6cf;--card:#fff;--ok:#1f7a4d;--warn:#a15c00;--bad:#b3261e;--acc:#245bdb}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#eeeee9;--muted:#9a9a93;--line:#34332f;--card:#201f1c;--ok:#59c48c;--warn:#e2a24a;--bad:#f08a82;--acc:#7fa6ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Noto Sans KR",sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.field{margin:6px 0}.field b{display:inline-block;min-width:5.5em;color:var(--muted);font-weight:600}
.hint{border-left:4px solid var(--acc)}.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
button{font:inherit;padding:10px 16px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
button.d{font-weight:700}button:focus-visible{outline:3px solid var(--acc)}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden}.bar i{display:block;height:100%;background:var(--acc)}
small{color:var(--muted)}
</style></head><body><main id="app"></main>
<script>
"use strict";
const DATA = __DATA__;
const app = document.getElementById("app");
const state = {i: -1, rows: [], t0: 0};
function el(tag, props, ...kids){const e=document.createElement(tag);for(const [k,v] of Object.entries(props||{})){if(k==="class")e.className=v;else e.setAttribute(k,v);}for(const k of kids){e.append(k instanceof Node?k:document.createTextNode(String(k)));}return e;}
function intro(){
  app.replaceChildren(
    el("h1",{},"광고 심사 연구"),
    el("div",{class:"card"},
      el("p",{},"가상의 광고 소재 ", DATA.trials.length, "건을 보고, 정책 문서(POLICY.md)에 따라 판정해 주세요: 승인(APPROVE) · 사람 검토 필요(REVIEW) · 반려(REJECT)."),
      el("p",{},"정답이 없는 소재도 있습니다. 아는 대로 판단하되, 너무 오래 고민하지 마세요. 소재마다 걸린 시간이 기록됩니다."),
      el("p",{},"일부 소재에는 시스템의 제안이 함께 보입니다. 제안은 틀릴 수 있습니다. 제안을 따를지는 스스로 정하세요."),
      el("p",{},"수집하는 것: 검토자 번호(", DATA.reviewer, "), 소재별 판정과 걸린 시간. 이름·계정은 수집하지 않습니다. 결과 파일은 이 기기에만 저장되고 직접 전달합니다."),
      el("small",{},"참여는 언제든 멈출 수 있습니다. 먼저 연습 ", DATA.practice.length, "건이 나오며 기록에 포함되지 않습니다.")
    ),
    el("div",{class:"btns"}, el("button",{class:"d",id:"go"},"시작"))
  );
  document.getElementById("go").onclick=()=>{state.i=-DATA.practice.length-1;next();};
}
function current(){return state.i<0 ? DATA.practice[DATA.practice.length+state.i] : DATA.trials[state.i];}
function show(){
  const t=current(), it=DATA.items[t.item], c=it.creative;
  const practice=state.i<0, total=DATA.trials.length;
  const fields=[["업종",c.advertiser.category],["광고주",c.advertiser.name+(c.advertiser.verified?" (인증됨)":" (인증 안 됨)")],["제목",c.headline],["본문",c.body],["CTA",c.cta],["고지",c.disclosure],["이미지 글자",c.image_text],["랜딩 글",c.landing_text]];
  const card=el("div",{class:"card"});
  for(const [k,v] of fields){ if(v){ card.append(el("div",{class:"field"},el("b",{},k),v)); } }
  const kids=[el("small",{},practice?("연습 "+(state.i+DATA.practice.length+1)+"/"+DATA.practice.length):((state.i+1)+" / "+total)),
    el("div",{class:"bar"},el("i",{style:"width:"+(practice?0:Math.round(100*(state.i)/total))+"%"})), card];
  if(t.condition==="assisted"){
    const h=el("div",{class:"card hint"},el("div",{},el("b",{},"시스템 제안: "),it.engine));
    if(it.rules.length){h.append(el("div",{},"발견된 규칙: ",it.rules.join(", ")));}
    for(const q of it.quotes){h.append(el("div",{},"· ",q[0]," — ",q[1],": “",q[2],"”"));}
    if(!it.rules.length){h.append(el("div",{},"발견된 위반 표현이 없습니다."));}
    kids.push(h);
  }
  const btns=el("div",{class:"btns"});
  for(const d of ["APPROVE","REVIEW","REJECT"]){const b=el("button",{class:"d"},d==="APPROVE"?"승인 (APPROVE)":d==="REVIEW"?"사람 검토 (REVIEW)":"반려 (REJECT)");b.onclick=()=>answer(d);btns.append(b);}
  kids.push(btns);
  app.replaceChildren(...kids);
  state.t0=performance.now();
}
function answer(d){
  const ms=Math.round(performance.now()-state.t0), t=current();
  if(state.i>=0){state.rows.push({reviewer:DATA.reviewer,n:t.n,item:t.item,condition:t.condition,block:t.block,decision:d,ms:ms});}
  next();
}
function next(){
  state.i++;
  if(state.i>=DATA.trials.length){finish();return;}
  show();
}
function finish(){
  const out={reviewer:DATA.reviewer,design:DATA.design_id,responses:state.rows};
  const blob=new Blob([JSON.stringify(out,null,1)],{type:"application/json"});
  const a=el("a",{href:URL.createObjectURL(blob),download:DATA.reviewer+".json"},"결과 파일 받기 ("+DATA.reviewer+".json)");
  app.replaceChildren(el("h1",{},"끝났습니다"),el("div",{class:"card"},el("p",{},"수고하셨습니다. 아래 파일을 연구 담당자에게 전달해 주세요."),el("p",{},a)));
}
intro();
</script></body></html>
"""


def booklet(reviewer: str, trials: Sequence[Trial], items: dict[str, StudyItem], practice: Sequence[StudyItem], design_id: str) -> str:
    """검토자 한 명의 책자. 제안(엔진 판정·발견)은 *그 검토자가 제안과 함께 보는* 소재에만 담는다 — 제안 없이 보는 소재의 제안이 페이지 소스에 남지 않게."""
    mine = [t for t in trials if t.reviewer == reviewer]
    prac_cond = [(p.id, "unassisted" if k % 2 == 0 else "assisted") for k, p in enumerate(practice)]
    prac = [{"n": 0, "item": i, "condition": c, "block": 0} for i, c in prac_cond]
    assisted = {i for i, c in prac_cond if c == "assisted"} | {t.item_id for t in mine if t.condition == "assisted"}
    pool = {**{p.id: p for p in practice}, **items}
    needed = {i for i, _ in prac_cond} | {t.item_id for t in mine}
    data = {
        "reviewer": reviewer, "design_id": design_id,
        "trials": [{"n": t.n, "item": t.item_id, "condition": t.condition, "block": t.block} for t in mine], "practice": prac,
        "items": {
            i: {"creative": pool[i].creative, **({"engine": pool[i].engine, "rules": list(pool[i].engine_rules), "quotes": [list(q) for q in pool[i].engine_quotes]} if i in assisted else {"engine": None, "rules": [], "quotes": []})}
            for i in sorted(needed)
        },
    }
    return PAGE.replace("__RID__", reviewer).replace("__DATA__", _safe_json(data))


def design_id(items: Sequence[StudyItem], trials: Sequence[Trial], seed: int) -> str:
    blob = json.dumps([[i.id for i in items], [(t.reviewer, t.item_id, t.condition) for t in trials], seed], ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


# --- 분석 --------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    reviewer: str
    item: str
    condition: str
    decision: str
    ms: int


def parse_responses(payload: dict[str, Any]) -> list[Response]:
    out = []
    for r in payload["responses"]:
        if r["decision"] not in DECISIONS:
            raise ValueError(f"알 수 없는 판정 {r['decision']!r}")
        if r["condition"] not in ("assisted", "unassisted"):
            raise ValueError(f"알 수 없는 조건 {r['condition']!r}")
        out.append(Response(payload["reviewer"], r["item"], r["condition"], r["decision"], int(r["ms"])))
    return out


def reviewer_metrics(responses: Sequence[Response], items: dict[str, StudyItem]) -> dict[str, dict[str, dict[str, float]]]:
    """검토자 → 조건 → 지표."""
    by: dict[str, dict[str, list[Response]]] = defaultdict(lambda: defaultdict(list))
    for r in responses:
        by[r.reviewer][r.condition].append(r)
    out: dict[str, dict[str, dict[str, float]]] = {}
    for who, conds in by.items():
        out[who] = {}
        for cond, rs in conds.items():
            gold = [(r, items[r.item]) for r in rs]
            risky = [(r, it) for r, it in gold if it.gold != "APPROVE"]
            wrong = [(r, it) for r, it in gold if it.stratum == "engine_wrong"]
            m: dict[str, float] = {
                "n": float(len(rs)),
                "accuracy": sum(r.decision == it.gold for r, it in gold) / len(gold),
                "safety_cost": sum(cost(it.gold, r.decision, "safety") for r, it in gold) / len(gold),
                "log_time": statistics.median(math.log(max(r.ms, 1)) for r in rs),
            }
            if risky:
                m["unsafe_approve"] = sum(r.decision == "APPROVE" for r, _ in risky) / len(risky)
            if wrong:
                m["follow_wrong"] = sum(r.decision == it.engine for r, it in wrong) / len(wrong)
                m["accuracy_on_wrong"] = sum(r.decision == it.gold for r, it in wrong) / len(wrong)
            out[who][cond] = m
    return out


def _paired(diffs: Sequence[float], *, boot: int, perm: int, seed: int) -> dict[str, float | int | None]:
    n = len(diffs)
    if n < 2:
        return {"n": n, "mean": diffs[0] if diffs else None, "ci_low": None, "ci_high": None, "p": None}
    rng = random.Random(seed)
    mean = statistics.fmean(diffs)
    means = sorted(statistics.fmean(rng.choices(diffs, k=n)) for _ in range(boot)) if boot else []
    if n <= 14:                                       # 정확 순열: 부호를 모두 뒤집어 본다
        extreme = total = 0
        for mask in range(2**n):
            s = sum(d if (mask >> i) & 1 else -d for i, d in enumerate(diffs)) / n
            extreme += abs(s) >= abs(mean) - 1e-12
            total += 1
        p = extreme / total
    else:
        extreme = sum(abs(statistics.fmean(d if rng.random() < 0.5 else -d for d in diffs)) >= abs(mean) - 1e-12 for _ in range(perm))
        p = (extreme + 1) / (perm + 1)
    return {"n": n, "mean": round(mean, 4), "ci_low": round(means[int(0.025 * boot)], 4) if means else None, "ci_high": round(means[int(0.975 * boot) - 1], 4) if means else None, "p": round(p, 4)}


METRICS = ("accuracy", "unsafe_approve", "safety_cost", "log_time", "follow_wrong", "accuracy_on_wrong")


def analyze(responses: Sequence[Response], items: dict[str, StudyItem], *, boot: int = 2000, perm: int = 20000, seed: int = 0) -> dict[str, Any]:
    pm = reviewer_metrics(responses, items)
    out: dict[str, Any] = {"reviewers": len(pm), "responses": len(responses), "metrics": {}}
    for m in METRICS:
        diffs = [c["assisted"][m] - c["unassisted"][m] for c in pm.values() if "assisted" in c and "unassisted" in c and m in c["assisted"] and m in c["unassisted"]]
        res = _paired(diffs, boot=boot, perm=perm, seed=seed)
        if m == "log_time" and res["mean"] is not None:
            res["time_ratio"] = round(math.exp(float(res["mean"])), 3)        # 중앙값 시간의 비(assisted / unassisted)
        out["metrics"][m] = res
    out["unassisted_mean"] = {m: round(statistics.fmean(c["unassisted"][m] for c in pm.values() if "unassisted" in c and m in c["unassisted"]), 4) for m in ("accuracy", "unsafe_approve", "follow_wrong") if any("unassisted" in c and m in c["unassisted"] for c in pm.values())}
    return out


# --- 시뮬레이션 참가자 -------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SimParams:
    base_accuracy: dict[str, float] | None = None       # 층별 제안 없을 때 정답률
    help_when_engine_right: float = 0.0                  # 엔진이 맞을 때 제안이 정답률을 올리는 폭(절대)
    follow_when_engine_wrong: float = 0.0                # 엔진이 틀릴 때 제안을 따를 확률(제안이 있을 때)
    speedup: float = 1.0                                 # 제안이 있을 때 시간 배수
    reviewer_sd: float = 0.06                            # 검토자별 정답률 편차
    base_seconds: float = 40.0

    def acc(self) -> dict[str, float]:
        return self.base_accuracy or {"clear_correct": 0.92, "gray_correct": 0.68, "engine_wrong": 0.62}


def simulate_responses(trials: Sequence[Trial], items: dict[str, StudyItem], params: SimParams, seed: int) -> list[Response]:
    rng = random.Random(seed)
    skill = {r: rng.gauss(0, params.reviewer_sd) for r in sorted({t.reviewer for t in trials})}
    speed = {r: rng.gauss(0, 0.25) for r in skill}
    out: list[Response] = []
    for t in trials:
        it = items[t.item_id]
        p = min(0.99, max(0.05, params.acc()[it.stratum] + skill[t.reviewer]))
        assisted = t.condition == "assisted"
        follows = False
        if assisted and it.stratum != "engine_wrong":
            p = min(0.99, p + params.help_when_engine_right)
        if assisted and it.stratum == "engine_wrong" and rng.random() < params.follow_when_engine_wrong:
            follows = True
        if follows:
            d = it.engine
        elif rng.random() < p:
            d = it.gold
        else:
            d = rng.choice([x for x in DECISIONS if x != it.gold])
        secs = params.base_seconds * (params.speedup if assisted else 1.0) * math.exp(speed[t.reviewer] + rng.gauss(0, 0.35))
        out.append(Response(t.reviewer, t.item_id, t.condition, d, round(secs * 1000)))
    return out


def power(items: Sequence[StudyItem], params: SimParams, reviewers: Sequence[int] = (8, 12, 16, 24), sims: int = 200, seed: int = 0) -> dict[str, Any]:
    """검토자 수에 따라 시뮬레이션 연구의 지표별 유의(p<0.05) 비율."""
    by = {i.id: i for i in items}
    out: dict[str, Any] = {"sims": sims, "params": {k: v for k, v in params.__dict__.items() if v is not None}, "by_reviewers": {}}
    for n in reviewers:
        hits = dict.fromkeys(("accuracy", "log_time", "follow_wrong", "unsafe_approve"), 0)
        for s in range(sims):
            trials = design(items, n, seed=seed + s)
            res = simulate_responses(trials, by, params, seed=seed * 1000 + s)
            pm = reviewer_metrics(res, by)
            for m in hits:
                diffs = [c["assisted"][m] - c["unassisted"][m] for c in pm.values() if m in c.get("assisted", {}) and m in c.get("unassisted", {})]
                r = _paired(diffs, boot=0, perm=3000, seed=s)
                hits[m] += r["p"] is not None and float(r["p"]) < 0.05
        out["by_reviewers"][str(n)] = {m: round(h / sims, 3) for m, h in hits.items()}
    return out
