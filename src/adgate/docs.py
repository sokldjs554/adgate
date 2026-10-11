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
from .eval.metrics import OUTCOMES

MARKER = re.compile(r"<!-- num:([^|>\s]+)\|([a-z0-9_]+) -->(.*?)<!-- /num -->", re.DOTALL)
SYSTEM_LABEL = {
    "full": "전체 파이프라인", "B0_all_human": "B0 전부 사람", "B1_blocklist": "B1 키워드 블록리스트",
    "-normalize": "− 정규화", "-exceptions": "− 예외(부정·범위·면책·근거)", "-challenge": "− 도전자", "-landing": "− 랜딩 대조",
    "full+judge": "전체 + LLM judge",
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
        order = ["B0_all_human", "B1_blocklist", "full", "-normalize", "-exceptions", "-challenge", "-landing", "full+judge"]
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
    else:
        out += [sec("LLM judge"), "", "이 저장소의 결과에는 LLM judge 측정이 없다(실호출 미실측). `adgate eval --judge anthropic` 으로 측정한다.", ""]
    return "\n".join(out)


def agreement_md(ag: dict[str, Any]) -> str:
    d = ag["decision_agreement"]
    r = ag["rule"]
    out = [
        "# 독립 라벨러 일치도 (자동 생성)",
        "",
        "> `adgate agreement` 의 산출물에서 만든다. 라벨러는 **정책 문서만 보고** 광고 문구에 판정과 위반을 붙인 별도 에이전트(LLM)다.",
        "> 설계자와 같은 모델 계열이므로 두 사람(사람) 사이의 일치도처럼 읽으면 안 된다. 정책 문구의 모호함을 찾는 데는 유용했다.",
        "",
        f"- 판정 일치: **{d['k']}/{d['n']}** ({d['value'] * 100:.1f}%), Cohen's κ = **{ag['kappa']:.3f}**",
        f"- 규칙 단위(소재×규칙) P/R/F1: {r['precision']:.3f} / {r['recall']:.3f} / **{r['f1']:.3f}** (tp {r['tp']}, fp {r['fp']}, fn {r['fn']})",
        "",
        "| 정답 \\ 라벨러 | " + " | ".join(OUTCOMES) + " |",
        "|---|" + "---:|" * 3,
    ]
    for g in OUTCOMES:
        out.append(f"| {g} | " + " | ".join(str(ag["confusion"][g][p]) for p in OUTCOMES) + " |")
    out += ["", "## 불일치", ""]
    if not ag["disagreements"]:
        out.append("없음.")
    for x in ag["disagreements"]:
        out += [
            f"### {x['id']} — {x['headline']}",
            f"- 설계자 {x['gold']} {x['gold_rules']} / 라벨러 {x['labeler']} {x['labeler_rules']} (라벨러 확신 {x['confidence']}/5)",
            f"- 라벨러 메모: {x['note']}",
            "",
        ]
    out += [
        "## 이 결과로 한 일",
        "",
        "- 불일치의 원인은 정책 문구였다. `AD-BRD-01` 의 '성인'이 *어른*과 *성인물* 어느 쪽인지 모호했다. 문구를 고쳤다(규칙 동작은 바꾸지 않았다). 설계자의 정답은 정책의 의도와 같아서 바꾸지 않았다.",
        "- 라벨러가 지적한 다른 모호함(HLT 의 적용 범위와 GUA 와의 구분, 고지가 있어도 위반인 보장 표현, LND 대조 단위, 심사 조작 시도 규칙의 부재)도 [POLICY.md](POLICY.md) 에 명확히 하거나 알려진 공백으로 적었다.",
        "",
    ]
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
    agree = _load(art / "agreement_test.json")
    if agree:
        src["agreement"] = agree
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
    tree: dict[str, Any] = {"results": src.get("results", {}), "agreement": src.get("agreement", {})}
    hist: dict[str, Any] = {}
    for k, v in src.get("_history_raw", {}).items():
        if v:
            hist[k] = {ds: systems for ds, systems in v["results"].items()}
    tree["history"] = hist
    tree["sensitivity"] = src.get("sensitivity", {})
    return tree


def build(root: Path) -> dict[Path, str]:
    """생성할 파일과 내용. README 등 표식 파일은 현재 내용을 읽어 표식만 채운다."""
    src = gather(root)
    files: dict[Path, str] = {}
    if "results" in src:
        files[root / "docs/RESULTS.md"] = results_md(src)
    if "agreement" in src:
        files[root / "docs/LABELER_AGREEMENT.md"] = agreement_md(src["agreement"])
    tree = lookup_sources(src)
    for path in (root / "README.md", root / "docs/DECISIONS.md", root / "docs/FAILURES.md", root / "docs/LIMITATIONS.md"):
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
