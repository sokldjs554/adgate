"""문서 생성. 문서의 수치는 산출물(JSON)에서만 채운다.

* ``docs/RESULTS.md`` 와 ``docs/LABELER_AGREEMENT.md`` 는 통째로 생성한다.
* README 등의 ``<!-- num:경로|형식 -->값<!-- /num -->`` 표식 안쪽을 산출물 값으로 바꾼다.
* ``--check`` 는 파일을 고치지 않고, 생성 결과가 현재 파일과 다르면 실패한다(CI 가 문서-산출물 불일치를 잡는다).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .eval import gate
from .eval.metrics import OUTCOMES, ratio

MARKER = re.compile(r"<!-- num:([^|>\s]+)\|([a-z0-9_]+) -->(.*?)<!-- /num -->", re.DOTALL)
SYSTEM_LABEL = {
    "full": "전체 파이프라인", "B0_all_human": "B0 전부 사람", "B1_blocklist": "B1 키워드 블록리스트",
    "-normalize": "− 정규화", "-exceptions": "− 예외(부정·범위·면책·근거)", "-challenge": "− 도전자", "-landing": "− 랜딩 대조",
    "full+judge": "전체 + LLM judge (녹음, 정책 등급 그대로)", "full+judge_cap": "전체 + LLM judge (녹음, REVIEW 까지만)",
}
DATASET_LABEL = {
    "dev": "dev (개발용)", "test": "test (손작성·동결)", "redteam": "redteam (독립 에이전트)", "generated": "generated (합성·in-grammar)",
}


def lookup(sources: dict[str, Any], path: str) -> Any:
    cur: Any = sources
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(f"{path}: '{part}' 없음")
    return cur


def fmt(value: Any, spec: str) -> str:
    if spec == "pct0":
        return f"{value * 100:.0f}%"
    if spec == "pct1":
        return f"{value * 100:.1f}%"
    if spec == "f3":
        return f"{value:.3f}"
    if spec == "f2":
        return f"{value:.2f}"
    if spec == "int":
        return f"{int(value)}"
    if spec == "kn":
        return f"{value['k']}/{value['n']}"
    if spec == "rpct1":
        return "-" if value["value"] is None else f"{value['value'] * 100:.1f}% ({value['k']}/{value['n']})"
    if spec == "ms":
        return f"{value:.2f} ms"
    if spec == "len":
        return f"{len(value)}"
    raise ValueError(f"알 수 없는 형식 {spec}")


def fill_markers(text: str, sources: dict[str, Any]) -> str:
    def sub(m: re.Match[str]) -> str:
        return f"<!-- num:{m.group(1)}|{m.group(2)} -->{fmt(lookup(sources, m.group(1)), m.group(2))}<!-- /num -->"

    return MARKER.sub(sub, text)


# --------------------------------------------------------------------------- 표 렌더링
def _r(x: dict[str, Any]) -> str:
    return fmt(x, "rpct1")


def _ci(x: dict[str, Any]) -> str:
    lo, hi = x["ci"]
    return f"[{lo * 100:.0f}–{hi * 100:.0f}%]"


def results_md(src: dict[str, Any]) -> str:
    meta = src["results_meta"]
    res: dict[str, dict[str, Any]] = src["results"]
    counter = iter(range(1, 20))

    def sec(title: str) -> str:
        return f"## {next(counter)}. {title}"

    out: list[str] = [
        "# 결과 (자동 생성)",
        "",
        "> 이 파일은 `adgate docs` 가 `artifacts/*.json` 에서 만든다. **손으로 고치지 않는다.**",
        f"> 측정 기준: git `{meta['git_sha']}`{' (작업트리 수정됨)' if meta['dirty'] else ''}, {meta['created']}, Python {meta['python']}.",
        "> 모든 소재는 합성이고 정책은 가상이다. `test` 는 같은 설계자가 쓴 셋이라 순수 holdout 이 아니고(첫 측정 뒤 수정함),",
        "> `generated` 는 생성기와 탐지기가 어휘를 공유해 낙관적이다. 수치를 실제 광고에서의 정확도로 읽지 않는다.",
        "",
        sec("전체 파이프라인의 핵심 지표"),
        "",
        "| 데이터셋 | N | 정확도 | 위험 승인율 (반려·보류 정답을 승인) | 반려 정답을 승인 | 정상 오반려 | 정상 오보류 | 자동 처리율 (상한) | 규칙 F1 | 인용 위치 | 인용 정확 |",
        "|---|---:|---:|---|---|---|---|---|---:|---|---|",
    ]
    for ds in ("dev", "test", "redteam", "generated"):
        if ds not in res or "full" not in res[ds]:
            continue
        m = res[ds]["full"]
        n = m["n"]
        ceiling = (n - m["gold_counts"]["REVIEW"]) / n
        out.append(
            f"| {DATASET_LABEL[ds]} | {n} | {m['accuracy']['value'] * 100:.1f}% | {_r(m['unsafe_approve'])} {_ci(m['unsafe_approve'])} | "
            f"{_r(m['hard_pass_through'])} | {_r(m['false_reject'])} {_ci(m['false_reject'])} | {_r(m['over_review'])} | "
            f"{m['auto_rate']['value'] * 100:.1f}% ({ceiling * 100:.1f}%) | {m['rule']['micro']['f1']:.3f} | "
            f"{_r(m['localization'])} | {_r(m['grounding'])} |"
        )
    out += [
        "",
        "구간은 Wilson 95%. 자동 처리율의 괄호는 정답이 REVIEW 가 아닌 소재의 비율, 즉 *도달 가능한 상한*이다(사람이 봐야 하는 소재는 자동 처리해서는 안 된다).",
        "",
        sec("시스템 비교와 어블레이션"),
        "",
    ]
    for ds in ("dev", "test", "redteam", "generated"):
        if ds not in res:
            continue
        out += [f"### {DATASET_LABEL[ds]}", "", "| 시스템 | 정확도 | 위험 승인율 | 정상 오반려 | 정상 오보류 | 자동 처리율 | 규칙 F1 |", "|---|---:|---|---|---|---:|---:|"]
        order = ["B0_all_human", "B1_blocklist", "full", "-normalize", "-exceptions", "-challenge", "-landing", "full+judge", "full+judge_cap"]
        for sname in order:
            if sname not in res[ds]:
                continue
            m = res[ds][sname]
            out.append(
                f"| {SYSTEM_LABEL[sname]} | {m['accuracy']['value'] * 100:.1f}% | {_r(m['unsafe_approve'])} | {_r(m['false_reject'])} | "
                f"{_r(m['over_review'])} | {m['auto_rate']['value'] * 100:.1f}% | {m['rule']['micro']['f1']:.3f} |"
            )
        out.append("")
    out += ["각 구성요소를 끄면 지표가 얼마나 나빠지는지가 그 구성요소가 한 일이다. 나빠지지 않으면 평가셋이 그것을 덮지 못한다는 뜻이다.", ""]

    hist = src.get("history", {})
    if hist:
        out += [sec("사전등록 합격 기준 점검 (docs/EVAL_PLAN.md 4절)"), ""]
        cols = [(k, v) for k, v in hist.items() if v]
        b1_by = src.get("b1_for", {})
        header = "| 기준 | " + " | ".join(label for label, _ in cols) + " |"
        out += [header, "|---|" + "---|" * len(cols)]
        rows: dict[str, list[str]] = {}
        desc: dict[str, str] = {}
        for label, m in cols:
            ds = "redteam" if "redteam" in label else "test"
            for c in gate.acceptance(m, b1_by.get(ds)):
                desc[c.id] = f"{c.id} {c.desc}"
                mark = "통과" if c.ok else ("**실패**" if c.kind == "required" else "미달")
                rows.setdefault(c.id, []).append(f"{mark}")
        for cid, cells in rows.items():
            out.append(f"| {desc[cid]} | " + " | ".join(cells) + " |")
        out += [
            "",
            "필수(H) 미달은 '배포 가능'이라 쓰지 않는다. 목표(S) 미달은 미달로 적는다. 열 이름은 측정 시점이다. 정확한 수치와 시각은 [MEASUREMENTS.md](MEASUREMENTS.md).",
            "",
        ]

    sl = res.get("test", {}).get("full", {}).get("slices", {})
    rl = res.get("redteam", {}).get("full", {}).get("slices", {})
    if sl or rl:
        out += [sec("슬라이스 (태그별)"), "", "| 데이터셋 | 태그 | N | 정확도 | 위험 승인율 |", "|---|---|---:|---:|---|"]
        for label, s in (("test", sl), ("redteam", rl)):
            for tag, v in s.items():
                out.append(f"| {label} | {tag} | {v['n']} | {v['accuracy']['value'] * 100:.0f}% | {_r(v['unsafe_approve'])} |")
        out.append("")

    sens = src.get("sensitivity")
    if sens:
        out += [sec("열화 대조군 (구성요소를 끄면 게이트가 실패하는가)"), "", "| 끈 구성요소 | 게이트가 감지했나 | 실패한 검사 |", "|---|---|---|"]
        for comp, info in sens.items():
            out.append(f"| {comp} | {'예' if info['caught'] else '**아니오 (gap)**'} | {', '.join(info['failed_checks']) or '-'} |")
        out.append("")

    out += [sec("혼동 행렬 (정답 → 예측)"), ""]
    for ds in ("dev", "test", "redteam", "generated"):
        if ds not in res or "full" not in res[ds]:
            continue
        c = res[ds]["full"]["confusion"]
        out += [f"**{DATASET_LABEL[ds]}**", "", "| 정답 \\ 예측 | " + " | ".join(OUTCOMES) + " |", "|---|" + "---:|" * 3]
        for g in OUTCOMES:
            out.append(f"| {g} | " + " | ".join(str(c[g][p]) for p in OUTCOMES) + " |")
        out.append("")

    spend = meta.get("judge_spend")
    if spend:
        out += [sec("LLM judge 사용량"), "", "```json", json.dumps(spend, ensure_ascii=False, indent=2), "```", ""]
    judge = src.get("judge")
    if judge:
        out += judge_replay_md(judge, sec("LLM judge — 녹음 재생 (실호출 아님)"))
    elif not spend:
        out += [sec("LLM judge"), "", "이 저장소의 결과에는 LLM judge 측정이 없다(실호출 미실측). `adgate eval --judge anthropic` 으로 측정한다.", ""]
    return "\n".join(out)


def pooled_judge(judge: dict[str, Any]) -> dict[str, Any]:
    """세 데이터셋을 합친 값. 정답 APPROVE 소재를 judge 가 건드린 비율이 판단 규칙의 근거라서 합계를 따로 낸다."""
    res, summ = judge["results"], judge["meta"]["judge_replay"]["datasets"]
    n_approve = sum(res[ds]["full"]["gold_counts"]["APPROVE"] for ds in summ)
    return {
        "approve_changed": ratio(sum(len(summ[ds]["approve_gold_changed"]) for ds in summ), n_approve),
        "approve_to_reject": ratio(sum(len(summ[ds]["approve_gold_to_reject"]) for ds in summ), n_approve),
        "unsafe_fixed": sum(len(summ[ds]["unsafe_fixed"]) for ds in summ),
        "items": sum(summ[ds]["n"] for ds in summ),
        "lost": sum(summ[ds]["deterministic_lost"] for ds in summ),
    }


def judge_replay_md(judge: dict[str, Any], heading: str) -> list[str]:
    meta, res = judge["meta"], judge["results"]
    rp = meta["judge_replay"]
    summ: dict[str, dict[str, Any]] = rp["datasets"]
    pool = pooled_judge(judge)
    out = [
        heading,
        "",
        "> **이 수치는 운영 모델의 성능이 아니다.** 프롬프트·스키마·근거 검증·병합·판정은 실제 파이프라인이지만, 모델 응답은 서브에이전트가 '모델 대역'으로 만든 녹음이다.",
        "> 설계자·작성자와 같은 모델 계열이고, 대역은 평가임을 안다(낙관 쪽 편향). 사전등록·방법·한계는 [JUDGE_REPLAY.md](JUDGE_REPLAY.md).",
        f"> 녹음 {rp['responses']}건(sha256 `{rp['file_sha256'][:12]}…`, 누락 {rp['misses']}), 측정 기준 git `{meta['git_sha']}`{' (작업트리 수정됨)' if meta['dirty'] else ''}, {meta['created']}.",
        "",
        "| 데이터셋 | 시스템 | N | 정확도 | 위험 승인율 | 정상 오반려 | 정상 오보류 | 자동 처리율 | 자동 처리 정밀도 |",
        "|---|---|---:|---:|---|---|---|---:|---|",
    ]
    for ds in summ:
        for sname in ("full", "full+judge", "full+judge_cap"):
            m = res[ds][sname]
            out.append(
                f"| {DATASET_LABEL[ds]} | {SYSTEM_LABEL[sname]} | {m['n']} | {m['accuracy']['value'] * 100:.1f}% | {_r(m['unsafe_approve'])} | "
                f"{_r(m['false_reject'])} | {_r(m['over_review'])} | {m['auto_rate']['value'] * 100:.1f}% | {_r(m['auto_precision'])} |"
            )
    out += [
        "",
        "자동 처리 정밀도 = 자동으로 승인·반려한 것 중 정답과 같은 것. 결정론만 쓸 때의 높은 자동 처리율에는 잘못된 자동 승인이 섞여 있다.",
        "",
        "| 데이터셋 | Judge 가 후보를 낸 소재 | 후보 수 | 근거 없는 의심 | 결정론 발견 손실 | 판정이 바뀐 소재 | 정답 APPROVE 가 바뀜 | 그중 REJECT |",
        "|---|---|---:|---|---:|---:|---|---|",
    ]
    for ds, s in summ.items():
        n_ap = res[ds]["full"]["gold_counts"]["APPROVE"]
        out.append(
            f"| {DATASET_LABEL[ds]} | {_r(s['judge_added'])} | {s['judge_findings']} | {_r(s['judge_ungrounded'])} | {s['deterministic_lost']} | "
            f"{len(s['changed'])} | {len(s['approve_gold_changed'])}/{n_ap} | {len(s['approve_gold_to_reject'])}/{n_ap} |"
        )
    pc, pr = pool["approve_changed"], pool["approve_to_reject"]
    out += [
        "",
        f"정답 APPROVE 소재 합계 {pc['n']}건 중 judge 때문에 판정이 바뀐 것 **{pc['k']}건** ({pc['value'] * 100:.1f}%, Wilson 95% [{pc['ci'][0] * 100:.1f}–{pc['ci'][1] * 100:.1f}%]), "
        f"그중 REJECT **{pr['k']}건**. 결정론이 놓친 위험 소재 중 judge 가 잡은 것 {pool['unsafe_fixed']}건.",
        "",
        "### Judge 가 정답 APPROVE 소재의 판정을 바꾼 경우",
        "",
        "| 소재 | 결정론 | + judge | + judge(REVIEW 까지만) | Judge 가 낸 규칙 |",
        "|---|---|---|---|---|",
    ]
    rows = [(ds, x) for ds, s in summ.items() for x in s["approve_gold_changed"]]
    for ds, x in rows:
        out.append(f"| {ds}/{x['id']} | {x['full']} | {x['judge']} | {x['judge_cap']} | {', '.join(x['llm_rules'])} |")
    if not rows:
        out.append("| (없음) | | | | |")
    out.append("")
    return out


def _agreement_block(title: str, ag: dict[str, Any], intro: str, outro: list[str]) -> list[str]:
    d = ag["decision_agreement"]
    r = ag["rule"]
    out = [
        f"## {title}",
        "",
        intro,
        "",
        f"- 판정 일치: **{d['k']}/{d['n']}** ({d['value'] * 100:.1f}%), Cohen's κ = **{ag['kappa']:.3f}**",
        f"- 규칙 단위(소재×규칙) P/R/F1: {r['precision']:.3f} / {r['recall']:.3f} / **{r['f1']:.3f}** (tp {r['tp']}, fp {r['fp']}, fn {r['fn']})",
        *(
            [f"- 인용 위치(정답에 인용이 있는 {q['gold_quoted']}개): 같은 필드에서 겹침 **{q['overlap']}**, 글자까지 같음 **{q['exact']}**"]
            if (q := ag.get("quote")) and q["gold_quoted"]
            else []
        ),
        "",
        "| 정답 \\ 라벨러 | " + " | ".join(OUTCOMES) + " |",
        "|---|" + "---:|" * 3,
    ]
    for g in OUTCOMES:
        out.append(f"| {g} | " + " | ".join(str(ag["confusion"][g][p]) for p in OUTCOMES) + " |")
    out += ["", "### 불일치", ""]
    if not ag["disagreements"]:
        out += ["없음.", ""]
    for x in ag["disagreements"]:
        out += [
            f"#### {x['id']} — {x['headline']}",
            f"- 설계자 {x['gold']} {x['gold_rules']} / 라벨러 {x['labeler']} {x['labeler_rules']} (라벨러 확신 {x['confidence']}/5)",
            f"- 라벨러 메모: {x['note']}",
            "",
        ]
    return [*out, *outro]


def agreement_md(test_ag: dict[str, Any] | None, redteam_ag: dict[str, Any] | None = None) -> str:
    out = [
        "# 독립 라벨러 일치도 (자동 생성)",
        "",
        "> `adgate agreement` 의 산출물에서 만든다. 라벨러는 **정책 문서만 보고, 정답을 모른 채** 광고 문구에 판정과 위반을 붙인 별도 에이전트(LLM)다.",
        "> 설계자·작성자와 같은 모델 계열이므로 사람 심사자 사이의 일치도처럼 읽으면 안 된다. 일치도가 높은 것은 정책이 명확하다는 증거일 수도,",
        "> 같은 계열이라 같은 방식으로 읽는다는 증거일 수도 있고 이 저장소로는 둘을 구분하지 못한다. 정책 문구의 모호함을 찾는 데는 유용했다.",
        "",
    ]
    if test_ag is not None:
        out += _agreement_block(
            "test (설계자가 쓴 77건)", test_ag, "`test` 소재를 라벨러 1명이 라벨링했다.",
            [
                "### 이 결과로 한 일",
                "",
                "- 불일치의 원인은 정책 문구였다. `AD-BRD-01` 의 '성인'이 *어른*과 *성인물* 어느 쪽인지 모호했다. 문구를 고쳤다(규칙 동작은 바꾸지 않았다). 설계자의 정답은 정책의 의도와 같아서 바꾸지 않았다.",
                "- 라벨러가 지적한 다른 모호함(HLT 의 적용 범위와 GUA 와의 구분, 고지가 있어도 위반인 보장 표현, LND 대조 단위, 심사 조작 시도 규칙의 부재)도 [POLICY.md](POLICY.md) 에 명확히 하거나 알려진 공백으로 적었다.",
                "",
            ],
        )
    if redteam_ag is not None:
        out += _agreement_block(
            "redteam (독립 작성자 8명이 쓴 72건)",
            redteam_ag,
            "`redteam` 소재의 정답·태그를 가리고 id 를 무작위로 바꿔 라벨러 3명에게 24건씩 나눠 맡겼다. 작성자와 라벨러는 서로를 모른다.",
            [
                "### 이 결과로 한 일",
                "",
                "- 판정과 규칙이 모두 일치해서 정답을 바꾸지 않았고, 불일치로 표시한 소재도 없다(불일치가 있었다면 설계자가 고르지 않고 기계적으로 `disputed` 태그를 붙여 두 수치를 모두 내기로 미리 정했다).",
                "- 인용은 구간의 길이를 라벨러가 다르게 고르는 경우가 있다(위 인용 위치 항목). 정책 8절 4항이 이를 정리한다.",
                "- 두 쪽이 *각자* 지적한 정책의 모호함은 [POLICY_GAPS.md](POLICY_GAPS.md) 에 모았다. 완전한 일치는 이 셋이 '정책으로 분명히 판정되는 경우'만 담도록 작성자에게 요청한 결과이기도 하다. **회색 지대 소재는 적다.**",
                "",
            ],
        )
    return "\n".join(out)


# --------------------------------------------------------------------------- 입출력
def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def gather(root: Path) -> dict[str, Any]:
    art = root / "artifacts"
    results = _load(art / "results.json")
    src: dict[str, Any] = {}
    if results:
        src["results"] = results["results"]
        src["results_meta"] = results["meta"]
    judge = _load(art / "results_judge.json")
    if judge:
        src["judge"] = judge
    agree = _load(art / "agreement_test.json")
    if agree:
        src["agreement"] = agree
    agree_rt = _load(art / "agreement_redteam.json")
    if agree_rt:
        src["agreement_redteam"] = agree_rt
    sens = _load(art / "sensitivity.json")
    if sens:
        src["sensitivity"] = sens
    hist: dict[str, Any] = {}
    b1_for: dict[str, Any] = {}
    for label, fname, ds in (
        ("test 첫 측정 (v0, 순수 holdout)", "test_v0_first_contact.json", "test"),
        ("test 수정 후 (holdout 아님)", "test_v01_after_t048_fix.json", "test"),
        ("redteam 첫 측정 (수정 전)", "redteam_first_contact.json", "redteam"),
        ("redteam 수정 후 (holdout 아님)", "redteam_after_fixes.json", "redteam"),
    ):
        h = _load(art / "history" / fname)
        if h:
            hist[label] = h["results"][ds]["full"]
            if "B1_blocklist" in h["results"][ds]:
                b1_for[ds] = h["results"][ds]["B1_blocklist"]
    if hist:
        src["history"] = hist
        src["b1_for"] = b1_for
    src["_history_raw"] = {
        k: _load(art / "history" / f) for k, f in (
            ("test_v0", "test_v0_first_contact.json"), ("test_v01", "test_v01_after_t048_fix.json"),
            ("redteam_v0", "redteam_first_contact.json"), ("redteam_v1", "redteam_after_fixes.json"),
        )
    }
    return src


def lookup_sources(src: dict[str, Any]) -> dict[str, Any]:
    """표식 경로가 읽는 트리: results.<ds>.<system>.<metric>, agreement.*, history.<key>.<ds>.full.*"""
    tree: dict[str, Any] = {
        "results": src.get("results", {}), "agreement": src.get("agreement", {}), "agreement_redteam": src.get("agreement_redteam", {}),
    }
    hist: dict[str, Any] = {}
    for k, v in src.get("_history_raw", {}).items():
        if v:
            hist[k] = {ds: systems for ds, systems in v["results"].items()}
    tree["history"] = hist
    tree["sensitivity"] = src.get("sensitivity", {})
    if "judge" in src:
        tree["judge"] = {**src["judge"], "pooled": pooled_judge(src["judge"])}
    return tree


def build(root: Path) -> dict[Path, str]:
    """생성할 파일과 내용. README 등 표식 파일은 현재 내용을 읽어 표식만 채운다."""
    src = gather(root)
    files: dict[Path, str] = {}
    if "results" in src:
        files[root / "docs/RESULTS.md"] = results_md(src)
    if "agreement" in src or "agreement_redteam" in src:
        files[root / "docs/LABELER_AGREEMENT.md"] = agreement_md(src.get("agreement"), src.get("agreement_redteam"))
    tree = lookup_sources(src)
    for path in (
        root / "README.md", root / "docs/DECISIONS.md", root / "docs/FAILURES.md", root / "docs/LIMITATIONS.md", root / "docs/JUDGE_REPLAY.md",
    ):
        if path.exists():
            files[path] = fill_markers(path.read_text(encoding="utf-8"), tree)
    return files


def run(root: Path, check: bool) -> int:
    files = build(root)
    stale = [p for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    if check:
        for p in stale:
            print(f"문서가 산출물과 다릅니다: {p.relative_to(root)}  (adgate docs 로 다시 생성하세요)")
        return 1 if stale else 0
    for p, text in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    print(f"문서 {len(files)}개 갱신: " + ", ".join(str(p.relative_to(root)) for p in files))
    return 0
