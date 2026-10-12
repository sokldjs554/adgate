"""명령줄 도구. 심사, 평가, 게이트, 데이터 생성, 일치도, 서버."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import prereg
from .config import ReviewConfig
from .eval import agreement, dataset, gate, judge_replay, report
from .eval import gen as generator
from .eval import metrics as eval_metrics
from .eval.dataset import Example
from .eval.runner import errors as eval_errors
from .eval.runner import run_system
from .eval.systems import Prediction, ablations, all_to_human, naive_blocklist, pipeline_system
from .judge_recorded import RecordedJudge, creative_key, export_prompts, prompt_bundle, record_live
from .models import Creative
from .pipeline import review
from .policy import load_policy

ROOT = Path.cwd()
FROZEN = ("test", "redteam", "gray")
DATASETS = ("dev", "test", "redteam", "gray", "generated")  # 알려진 이름만 받는다('./test' 같은 경로로 --measure 가드를 피하지 못하게)
# 게이트가 회귀 감시에 쓰는 셋. 합격 기준(H1-H3)은 test 에만 건다. redteam 은 첫 접촉이 history 에 보존돼 있고 이후 수치는 holdout 이 아니므로,
# 정상 유사를 하나라도 놓치거나 오반려하면 바로 잡는 회귀 감시용으로 쓴다(리뷰 반영 중 R-002 오반려를 게이트가 못 잡았다 — FAILURES F-18).
GATE_DATASETS = ("dev", "test", "redteam")
REPLAY_SYSTEMS = ("full", "full+judge", "full+judge_cap")  # 녹음 재생 요약이 짝지어 보는 구성(docs/JUDGE_REPLAY.md 3절)


def _path(name: str, data_dir: Path) -> Path:
    return data_dir / f"{name}.jsonl"


def load_dataset(name: str, data_dir: Path) -> list[Example]:
    if name == "generated":
        return generator.generate(600, 7)
    return dataset.load(_path(name, data_dir))


def cmd_review(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    creative = Creative.model_validate(json.loads(raw))
    result = review(creative, ReviewConfig())
    if args.json:
        print(result.model_dump_json(indent=2))
        return 0
    print(f"[{result.outcome}] {creative.ad_id}  (정책 {result.policy_version}, {result.stats.latency_ms} ms)")
    for r in result.reasons:
        where = f"{r.field}: “{r.quote}”" if r.quote else "(광고 전체)"
        print(f"  - [{r.severity}] {r.rule_id} {r.title} — {where}\n      {r.message}\n      수정: {r.fix}")
    return 0


def _check_dataset_names(names: list[str]) -> None:
    unknown = [n for n in names if n not in DATASETS]
    if unknown:
        raise SystemExit(f"알 수 없는 데이터셋: {', '.join(unknown)} (가능: {', '.join(DATASETS)})")


def _verify_frozen(data_dir: Path, names: list[str], allow_modified: bool) -> None:
    """동결 셋을 쓰려면 매니페스트가 있고, 그 파일이 목록에 있고, 해시가 같아야 한다. 하나라도 아니면 거부한다(열린 채로 실패하지 않는다)."""
    if not names:
        return
    manifest = data_dir / "MANIFEST.sha256"
    if not manifest.exists():
        raise SystemExit(f"{manifest} 가 없습니다. 동결 셋({', '.join(names)})은 매니페스트로 해시를 확인할 수 있을 때만 씁니다.")
    status = dataset.verify_manifest(manifest)
    for name in names:
        fname = f"{name}.jsonl"
        if fname not in status:
            raise SystemExit(f"{fname} 이(가) {manifest.name} 에 없습니다. 동결하지 않은 파일은 동결 셋으로 쓰지 않습니다.")
        if not status[fname] and not allow_modified:
            raise SystemExit(
                f"{fname} 이(가) 동결 시점(MANIFEST.sha256)과 다릅니다. 라벨 오류 수정이면 --allow-modified-frozen 과 메모를 쓰세요."
            )


def cmd_eval(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    names = [n for n in args.datasets.split(",") if n]
    _check_dataset_names(names)
    frozen_used = [n for n in names if n in FROZEN]
    if frozen_used and not args.measure:
        raise SystemExit(f"{','.join(frozen_used)} 측정은 --measure 가 필요합니다(측정 기록이 자동으로 남습니다).")
    _verify_frozen(data_dir, frozen_used, args.allow_modified_frozen)

    systems: dict[str, Any] = {"B0_all_human": all_to_human(), "B1_blocklist": naive_blocklist(), **ablations()}
    judge_obj = None
    recorded: RecordedJudge | None = None
    if args.judge == "anthropic":
        from .judge_anthropic import AnthropicJudge

        judge_obj = AnthropicJudge(max_usd=args.max_usd)
        systems["full+judge_cap"] = pipeline_system(ReviewConfig(judge=judge_obj, judge_cap_review=True))  # 기본 구성(D-14)
    elif args.judge == "recorded":
        if not args.judge_file:
            raise SystemExit("--judge recorded 는 --judge-file 이 필요합니다")
        recorded = RecordedJudge.from_file(args.judge_file)
    wanted = set(args.systems.split(",")) if args.systems else None

    results: dict[str, dict[str, Any]] = {}
    ds_meta: dict[str, dict[str, Any]] = {}
    error_dump: dict[str, Any] = {}
    replay_preds: dict[str, dict[str, list[Prediction]]] = {}  # 녹음 재생 요약이 소재별로 짝지어 보는 세 구성의 예측
    replay_examples: dict[str, list[Example]] = {}
    sha, dirty = report.git_state(ROOT)
    for name in names:
        examples = load_dataset(name, data_dir)
        problems = dataset.validate(examples)
        if problems:
            raise SystemExit("라벨 일관성 오류:\n" + "\n".join(problems[:10]))
        ds_meta[name] = {"n": len(examples)}
        if name != "generated":
            ds_meta[name]["sha256"] = dataset.sha256_file(_path(name, data_dir))
        results[name] = {}
        ds_systems = dict(systems)
        if recorded is not None:
            if all(recorded.has(ex.creative) for ex in examples):
                ds_systems["full+judge"] = pipeline_system(ReviewConfig(judge=recorded, judge_cap_review=False))
                ds_systems["full+judge_cap"] = pipeline_system(ReviewConfig(judge=recorded, judge_cap_review=True))
            else:
                print(f"[{name}] 녹음이 모든 소재를 덮지 못해 judge 구성은 이 데이터셋에서 건너뜁니다")
        if wanted is not None:
            # 동결 셋은 `full` 을 항상 돌린다 — 측정 기록(MEASUREMENTS.md)이 `full` 한 줄로 남기 때문이다.
            ds_systems = {k: v for k, v in ds_systems.items() if k in wanted or (name in FROZEN and k == "full")}
        for sname, system in ds_systems.items():
            preds = run_system(system, examples)
            results[name][sname] = eval_metrics.compute(examples, preds)
            if sname == "full":
                error_dump[name] = eval_errors(examples, preds)
            if recorded is not None and sname in REPLAY_SYSTEMS:
                replay_preds.setdefault(name, {})[sname] = preds
                replay_examples[name] = examples
        if name in FROZEN and "full" in results[name]:
            report.append_measurement(Path(args.measurements), name, "full", sha, dirty, results[name]["full"], args.note)

    out = {"meta": report.meta(ROOT, ds_meta, (sha, dirty)), "results": results}
    if judge_obj is not None:
        out["meta"]["judge_spend"] = judge_obj.spend.summary()
    if recorded is not None:
        out["meta"]["judge_replay"] = {
            "responses": len(recorded.responses),
            "file_sha256": dataset.sha256_file(args.judge_file),
            "misses": recorded.misses,
            "errors": len(recorded.errors),
            "datasets": {
                name: judge_replay.summarize(replay_examples[name], t["full"], t["full+judge"], t["full+judge_cap"])
                for name, t in replay_preds.items()
                if all(k in t for k in REPLAY_SYSTEMS)
            },
        }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.errors_out).write_text(json.dumps(error_dump, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in names:
        full = results[name].get("full")
        if full:
            print(
                f"{name:10} n={full['n']:4} acc={full['accuracy']['value']} unsafe={full['unsafe_approve']['value']} "
                f"FR={full['false_reject']['value']} over={full['over_review']['value']} auto={full['auto_rate']['value']} "
                f"ruleF1={full['rule']['micro']['f1']}"
            )
    print(f"→ {out_path}")
    return 0


def _print_checks(checks: list[gate.Check]) -> None:
    for c in checks:
        mark = "OK  " if c.ok else ("FAIL" if c.kind != "target" else "MISS")
        print(f"  [{mark}] {c.kind:10} {c.id:24} {c.desc}  value={c.value} threshold={c.threshold}")


def cmd_gate(args: argparse.Namespace) -> int:
    """회귀 감시. `test` 를 매번 읽지만 측정으로 세지 않는다(CI 가 기록을 커밋할 수 없다) → test 는 첫 측정 뒤 holdout 이 아니다(docs/DECISIONS.md D-5)."""
    data_dir = Path(args.data_dir)
    names = [n for n in GATE_DATASETS if _path(n, data_dir).exists()]
    if "test" not in names or "dev" not in names:
        raise SystemExit("게이트에는 dev 와 test 가 필요합니다.")
    _verify_frozen(data_dir, [n for n in names if n in FROZEN], args.allow_modified_frozen)
    datasets = {n: dataset.load(_path(n, data_dir)) for n in names}
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8")) if Path(args.baseline).exists() else None

    if args.update_baseline:
        out = {n: gate.snapshot(eval_metrics.compute(exs, run_system(pipeline_system(), exs))) for n, exs in datasets.items()}
        Path(args.baseline).parent.mkdir(parents=True, exist_ok=True)
        Path(args.baseline).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"기준선 갱신 → {args.baseline}")
        return 0

    checks = gate.run_gate(datasets, pipeline_system(), baseline)
    _print_checks(checks)
    bad = gate.failed(checks)
    if args.sensitivity:
        print("열화 대조군(구성요소를 끄면 게이트가 실패해야 정상):")
        sens = gate.sensitivity(datasets, baseline)
        for comp, info in sens.items():
            print(f"  -{comp:11} {'감지됨' if info['caught'] else 'GAP: 게이트가 감지하지 못함'} {info['failed_checks']}")
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(sens, ensure_ascii=False, indent=2), encoding="utf-8")
    print("게이트:", "통과" if not bad else f"실패 ({len(bad)}건)")
    return 0 if not bad else 1


def cmd_gen(args: argparse.Namespace) -> int:
    examples = generator.generate(args.n, args.seed)
    with Path(args.out).open("w", encoding="utf-8") as f:
        for ex in examples:
            f.write(ex.model_dump_json() + "\n")
    print(f"{len(examples)}건 → {args.out}")
    return 0


def cmd_agreement(args: argparse.Namespace) -> int:
    examples = dataset.load(args.gold)
    res = agreement.compare(examples, agreement.load_labels(args.labels))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"판정 일치 {res['decision_agreement']['k']}/{res['n']}  κ={res['kappa']}  "
        f"규칙 F1={res['rule']['f1']}  불일치 {len(res['disagreements'])}건"
    )
    return 0


def cmd_agreement_multi(args: argparse.Namespace) -> int:
    examples = dataset.load(args.gold)
    alt: dict[str, str] = {}
    if args.notes and Path(args.notes).exists():
        for line in Path(args.notes).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("alt_decision"):
                    alt[row["id"]] = row["alt_decision"]
    res = agreement.compare_multi(examples, agreement.load_multi(args.labels), alt=alt)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"라벨러 {len(res['labelers'])}명 평균 일치 {res['mean_agreement_with_author']:.1%}  Fleiss κ(라벨러) {res['fleiss_kappa_labelers']}  "
        f"Fleiss κ(작성자 포함) {res['fleiss_kappa_with_author']}  분쟁 {res['disputed']['n']}건"
    )
    return 0


def cmd_llm(args: argparse.Namespace) -> int:
    """LLM 측정 묶음(suite)의 dry-run·점검·녹음·재생·오프라인 팔. 정의는 adgate/suites.py."""
    from . import suites

    if args.action == "list":
        for n in suites.names():
            s = suites.get(n)
            print(f"{n:16} {s.describe}  [{s.prereg}]  변형: {', '.join(f'{v.name}={v.model}' for v in s.variants.values())}")
        return 0
    if args.action == "spend":
        from .llm import ledger

        res = ledger.build()
        out = Path(args.out or "artifacts/llm_spend.json")
        suites.dump(res, out)
        print(f"실제 호출 {res['total']['calls']}회, 추정 비용 ${res['total']['usd']:.4f} → {out}")
        return 0
    suite = suites.get(args.suite)
    variants = [args.variant] if args.variant else list(suite.variants)
    rc = 0
    for v in variants:
        if v not in suite.variants:
            raise SystemExit(f"알 수 없는 변형 {v} (가능: {', '.join(suite.variants)})")
    if args.action == "offline":
        if suite.offline is None:
            raise SystemExit("이 suite 에는 오프라인 팔이 없습니다")
        res = {"meta": {"suite": suite.name, "git": report.git_state(ROOT)[0], "dirty": report.git_state(ROOT)[1], "prereg": suite.prereg}, "results": suite.offline()}
        out = Path(args.out or f"artifacts/results_{suite.name}__offline.json")
        suites.dump(res, out)
        print(f"오프라인 팔 → {out}")
        return 0
    for v in variants:
        if args.action == "dry-run":
            d = suites.dry_run(suite, v)
            print(f"[{suite.name}/{v}] 사례 {d['cases']}건, 호출 {d['calls']}회, 추정 비용 상한 ${d['estimated_usd_upper_bound']:.2f} ({d['model']})  대역 실패 {d['stand_in_failed']}")
            for t, row in d["by_task"].items():
                print(f"    {t:20} {row['calls']:4}회  ${row['usd']:.3f}")
            print("   ", d["note"])
            if args.out:
                suites.dump(d, Path(args.out))
        elif args.action == "check":
            r = suites.check(suite, v, max_usd=args.max_usd)
            print(f"[{suite.name}/{v}] 점검: 사례 {r['completed']}/{r['attempted']} 완료, 오류 {r['case_errors']}, 실제 호출 {r['live_calls']}, {r['spend']}")
            if r["case_errors"] or r["completed"] < r["attempted"]:     # 사례가 예외로 끝났거나 계정·예산 때문에 부르지 못했다: 점검이 되지 않았다
                return 1
        elif args.action == "record":
            r = suites.record(suite, v, max_usd=args.max_usd, workers=args.workers, retry_technical=args.retry_technical)
            print(f"[{suite.name}/{v}] 녹음: {r}")
            if r["skipped_budget"] or r["case_errors"]:      # 중간에 멈췄다(상한·계정) 또는 사례가 예외로 끝났다: 완성된 녹음이 아니다 — 작업이 실패로 보이게 한다(녹음은 그대로 올라간다)
                print(f"[{suite.name}/{v}] 녹음이 끝까지 가지 못했습니다(멈춘 사례 {r['skipped_budget']}, 예외 {r['case_errors']}). 원인을 고친 뒤 같은 명령을 다시 돌리면 이어서 부릅니다.")
                rc = 1
        elif args.action == "analyze":
            res = suites.analyze(suite, v)
            out = Path(args.out or f"artifacts/analysis_{suite.name}__{v}.json")
            suites.dump(res, out)
            print(f"[{suite.name}/{v}] 실패 분석 → {out}")
        elif args.action == "replay":
            res = suites.replay(suite, v)
            out = Path(args.out or f"artifacts/results_{suite.name}__{v}.json")
            suites.dump(res, out)
            m = res["meta"]
            print(f"[{suite.name}/{v}] 재생 → {out}  (사례 {m['cases']}, 오류 {len(m['case_errors'])}, 녹음 없음 {m['misses']})")
            if m["recorded_failures"]:       # 녹음에 실패 행이 있다: 모델이 낸 실패인지 환경이 낸 실패인지 *집계 전에* 호출 기록(.calls.jsonl)의 detail 로 확인한다
                print(f"  ! 녹음의 실패 행 {sum(m['recorded_failures'].values())}건: {m['recorded_failures']} — 환경 오류(크레딧·인증·시간 초과)면 측정이 아니다(F-28)")
    return rc


def cmd_demo(args: argparse.Namespace) -> int:
    """통합 데모: 셋업 대화 → 소재 생성 → 심사 → 성과 진단 → 고리 닫기(docs/DEMO.md). 데모이지 측정이 아니다."""
    from . import demo

    res = demo.run_demo(args.mode, variant=args.variant or None, scenario_id=args.scenario, product_id=args.product)
    if args.out:
        demo.dump(res, Path(args.out))
        print(f"전사 → {args.out}")
    if args.print:
        print(demo.transcript(res))
    bad = [c["name"] for c in res["checks"] if not c["ok"]]
    print(f"이음새 검사 {len(res['checks']) - len(bad)}/{len(res['checks'])} 통과" + (f"; 실패: {bad}" if bad else ""))
    for step, why in res["errors"].items():
        print(f"단계 {step} 를 돌리지 못했습니다: {why}")
    return 1 if bad or res["errors"] else 0


def cmd_prereg(args: argparse.Namespace) -> int:
    if args.register:
        e = prereg.register(args.register, args.start, args.end, note=args.note or "")
        print(f"등록: {e['doc']} {e['start']!r}~{e['end']!r} sha256={e['sha256'][:16]}…")
        return 0
    errs = prereg.check()
    for m in errs:
        print("어긋남:", m)
    print(f"사전등록 {len(prereg.load())}건 " + ("모두 그대로" if not errs else f"중 {len(errs)}건 어긋남"))
    return 1 if errs else 0


def cmd_validate(args: argparse.Namespace) -> int:
    """평가셋 파일의 형식과 라벨 일관성을 검사한다. --drop-invalid 면 통과한 줄만 --out 에 쓴다(내용을 고치지는 않는다)."""
    kept: list[str] = []
    bad = 0
    for n, line in enumerate(Path(args.file).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            example = dataset.Example.model_validate(json.loads(line))
            errors = dataset.validate([example])
        except Exception as exc:  # noqa: BLE001
            errors = [f"형식 오류: {type(exc).__name__}: {str(exc)[:120]}"]
        if errors:
            bad += 1
            print(f"줄 {n}: " + "; ".join(errors))
        else:
            kept.append(line)
    ids = [json.loads(x)["id"] for x in kept]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        bad += len(dup)
        print(f"중복 id: {sorted(dup)}")
    print(f"유효 {len(kept)}건, 문제 {bad}건")
    if args.drop_invalid and args.out:
        Path(args.out).write_text("\n".join(kept) + "\n", encoding="utf-8")
        print(f"→ {args.out}")
    return 0 if bad == 0 else 1


def cmd_docs(args: argparse.Namespace) -> int:
    from . import docs

    return docs.run(Path(args.root), args.check)


def cmd_judge_check(args: argparse.Namespace) -> int:
    """키가 있는 환경에서 Judge 한 건을 실제로 호출해 본다(요청·응답 계약 확인용)."""
    from .judge_anthropic import AnthropicJudge
    from .models import Advertiser

    if not _has_api_key():
        print(NO_KEY_MESSAGE, file=sys.stderr)
        return 2
    judge = AnthropicJudge(max_usd=args.max_usd, model=args.model or None, timeout=args.timeout)
    creative = Creative(
        ad_id="judge-check", advertiser=Advertiser(name="점검용", category="invest", verified=True),
        headline="손해 볼 일 없는 투자", body="누구나 안심하고 시작하세요.", cta="가입하기",
        disclosure="투자 상품은 원금 손실이 발생할 수 있습니다.",
    )
    base = review(creative, ReviewConfig())
    result = review(creative, ReviewConfig(judge=judge))
    print(f"결정론 경로: {base.outcome}  /  judge 포함: {result.outcome}  escalations={result.escalations}")
    for f in result.findings:
        if f.source == "llm":
            print(f"  LLM 후보 {f.rule_id}/{f.code} [{f.field}] “{f.quote}” confidence={f.confidence} — {f.note}")
    print("호출 정보:", json.dumps(judge.last_meta, ensure_ascii=False))
    print("사용량:", json.dumps(judge.spend.summary(), ensure_ascii=False))
    return 0 if not result.stats.judge_failed else 1


NO_KEY_MESSAGE = (
    "ANTHROPIC_API_KEY(또는 ANTHROPIC_AUTH_TOKEN)가 환경에 없습니다. 이 명령은 실제로 API 를 호출하고 비용이 듭니다. "
    "키를 채팅에 붙여 넣지 말고 환경 변수나 저장소 비밀값(Actions secrets)으로 넣으세요."
)


def _has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def cmd_judge_record(args: argparse.Namespace) -> int:
    """Judge 를 소재마다 한 번씩 *실제로* 호출해 응답을 녹음한다(키 필요, 비용 발생). 녹음은 ``eval --judge recorded`` 로 재생·집계한다."""
    from .judge_anthropic import AnthropicJudge, system_prompt

    data_dir = Path(args.data_dir)
    names = [n for n in args.datasets.split(",") if n]
    _check_dataset_names(names)
    _verify_frozen(data_dir, [n for n in names if n in FROZEN], False)
    policy = load_policy()
    creatives: dict[str, Creative] = {}
    for name in names:
        for ex in load_dataset(name, data_dir):
            creatives.setdefault(creative_key(ex.creative), ex.creative)
    if args.limit:
        creatives = dict(list(creatives.items())[: args.limit])

    if args.dry_run:  # 호출하지 않는다. 어댑터의 비용 추정식(보수적)으로 총액을 미리 본다.
        probe = AnthropicJudge(model=args.model or None, client=object(), max_usd=None)
        sys_chars = len(system_prompt(policy))
        prompts = export_prompts(list(creatives.values()), policy, lambda j: ReviewConfig(judge=j))
        total = sum(probe.spend.estimate(sys_chars + len(p["user"])) for p in prompts)
        print(f"[dry-run] 모델 {probe.model} · 소재 {len(prompts)}건 · 호출 {len(prompts)}회 · 추정 비용 ${total:.2f} (입력은 글자당 토큰 1개, 출력은 호출당 800토큰으로 본 보수적 추정)")
        print(f"          상한 --max-usd {args.max_usd:.2f}. 실제 비용은 생각(thinking) 토큰 때문에 달라질 수 있어, 상한에 닿으면 멈추고 지금까지의 녹음을 보존한다.")
        return 0
    if not _has_api_key():
        print(NO_KEY_MESSAGE, file=sys.stderr)
        return 2

    judge = AnthropicJudge(max_usd=args.max_usd, model=args.model or None, effort=args.effort, timeout=args.timeout)
    print(f"모델 {judge.model} · effort {args.effort} · 소재 {len(creatives)}건 · 동시 {args.workers} · 상한 ${args.max_usd:.2f}")
    result = record_live(
        creatives, judge, policy, out=Path(args.out), log=Path(args.log), workers=args.workers, retry_technical=args.retry_technical,
    )
    sha, dirty = report.git_state(ROOT)
    summary = {
        **judge.spend.summary(), "effort": args.effort, "timeout_s": args.timeout, "workers": args.workers, "datasets": names,
        "git_sha": sha, "dirty": dirty, **result,
    }
    summary_path = Path(args.log).with_name("summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"→ {args.out} (녹음) · {args.log} (호출별 기록) · {summary_path}")
    return 0 if result["failed"] == 0 and result["skipped_budget"] == 0 else 1


def _load_recording_rows(path: str) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if ln.strip():
            row = json.loads(ln)
            rows[row["key"]] = row
    return rows


def cmd_judge_compare(args: argparse.Namespace) -> int:
    """두 녹음(예: 대역 대 실호출)을 소재별로 견준다. 후보를 냈는가, 낸 규칙이 같은가."""
    res = judge_replay.compare_recordings(_load_recording_rows(args.a), _load_recording_rows(args.b))
    ag = res["agreement"]
    print(
        f"같은 소재 {res['n']}건 중 비교 {res['compared']}건: 둘 다 없음 {res['agree_empty']} · 둘 다 있음 {res['agree_nonempty']}(규칙 집합 같음 {res['rule_sets_equal']}) · "
        f"a 만 {res['only_a']} · b 만 {res['only_b']} · 실패 a {res['error_a']} / b {res['error_b']} → 일치 {ag['k']}/{ag['n']}"
    )
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_judge_export(args: argparse.Namespace) -> int:
    """Judge 에게 보낼 프롬프트를 내보낸다(녹음용). 어느 소재가 어느 데이터셋인지는 index.json 에만 있고, 조각 파일에는 없다."""
    import random

    data_dir = Path(args.data_dir)
    names = [n for n in args.datasets.split(",") if n]
    _verify_frozen(data_dir, [n for n in names if n in FROZEN], False)
    policy = load_policy()
    creatives: dict[str, Creative] = {}
    index: dict[str, list[str]] = {}
    for name in names:
        for ex in load_dataset(name, data_dir):
            key = creative_key(ex.creative)
            if key not in creatives:
                creatives[key] = ex.creative
            index.setdefault(key, []).append(f"{name}/{ex.id}")
    keys = list(creatives)
    prompts = export_prompts([creatives[k] for k in keys], policy, lambda j: ReviewConfig(judge=j))
    random.Random(args.seed).shuffle(prompts)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for fname, text in prompt_bundle(policy).items():
        (out / fname).write_text(text, encoding="utf-8")
    shards = [prompts[i::args.shards] for i in range(args.shards)]
    for i, shard in enumerate(shards, start=1):
        (out / f"shard_{i}.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in shard), encoding="utf-8")
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"소재 {len(prompts)}건 → {out} (조각 {args.shards}개, 시스템 프롬프트·스키마 포함)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .settings import Settings

    try:
        settings = Settings.from_env()  # 토큰이 없으면 시작하지 않는다. 트레이스백 대신 안내를 낸다.
    except (RuntimeError, ValueError) as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2
    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    if settings.dev and not loopback and os.environ.get("ADGATE_DEV_ALLOW_PUBLIC") != "1":
        print(
            f"설정 오류: ADGATE_DEV=1 은 누구나 아는 데모 토큰(심사자 포함)을 켭니다. 루프백이 아닌 주소({args.host})에는 열지 않습니다. "
            "컨테이너 안에서 일부러 쓰려면 ADGATE_DEV_ALLOW_PUBLIC=1 을 함께 설정하세요.",
            file=sys.stderr,
        )
        return 2
    uvicorn.run("adgate.api:create_app", factory=True, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adgate")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("review", help="소재 JSON 한 건을 심사한다")
    s.add_argument("file", help="JSON 파일 경로 또는 - (표준입력)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("eval", help="데이터셋 × 시스템 평가")
    s.add_argument("--datasets", default="dev,generated")
    s.add_argument("--systems", default="")
    s.add_argument("--data-dir", default="data")
    s.add_argument("--out", default="artifacts/results.json")
    s.add_argument("--errors-out", default="artifacts/errors.json")
    s.add_argument("--measurements", default="docs/MEASUREMENTS.md")
    s.add_argument("--measure", action="store_true", help="test/redteam 을 측정한다(기록이 자동으로 남는다)")
    s.add_argument("--allow-modified-frozen", action="store_true")
    s.add_argument("--note", default="")
    s.add_argument(
        "--judge", choices=["", "anthropic", "recorded"], default="",
        help="LLM judge 를 켠 구성을 함께 측정한다. anthropic: 실호출(키 필요, 비용 발생)로 기본 구성('full+judge_cap': Judge 발견은 REVIEW 까지만)을 잰다. "
        "recorded: --judge-file 의 녹음을 재생해 'full+judge'(정책 등급 그대로)와 'full+judge_cap' 을 함께 잰다",
    )
    s.add_argument("--judge-file", default="", help="--judge recorded 가 재생할 녹음(JSONL: key, response)")
    s.add_argument("--max-usd", type=float, default=2.0, help="--judge 사용 시 추정 비용 상한")
    s.set_defaults(fn=cmd_eval)

    s = sub.add_parser("gate", help="합격 기준과 기준선 회귀 검사")
    s.add_argument("--data-dir", default="data")
    s.add_argument("--baseline", default="artifacts/baseline.json")
    s.add_argument("--update-baseline", action="store_true")
    s.add_argument("--sensitivity", action="store_true")
    s.add_argument("--json-out", default="")
    s.add_argument("--allow-modified-frozen", action="store_true")
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("gen", help="시드 고정 합성 셋 생성")
    s.add_argument("--n", type=int, default=600)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--out", default="data/generated.jsonl")
    s.set_defaults(fn=cmd_gen)

    s = sub.add_parser("agreement", help="독립 라벨러와의 일치도")
    s.add_argument("--gold", default="data/test.jsonl")
    s.add_argument("--labels", default="data/labeler_test.jsonl")
    s.add_argument("--out", default="artifacts/agreement_test.json")
    s.set_defaults(fn=cmd_agreement)
    s = sub.add_parser("llm", help="LLM 측정 묶음(suite): list · offline · dry-run · check · record · replay · analyze(실패 분석) · spend(비용 장부)")
    s.add_argument("action", choices=["list", "offline", "dry-run", "check", "record", "replay", "analyze", "spend"])
    s.add_argument("suite", nargs="?", default="")
    s.add_argument("--variant", default="", help="변형(모델) 이름. 비우면 모두")
    s.add_argument("--max-usd", type=float, default=5.0)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--retry-technical", action="store_true")
    s.add_argument("--out", default="")
    s.set_defaults(fn=cmd_llm)
    s = sub.add_parser("demo", help="통합 데모: 셋업 대화 → 소재 생성 → 심사 → 성과 진단 → 고리 닫기(측정이 아니다)")
    s.add_argument("--mode", choices=["offline", "recorded"], default="offline", help="offline = 모델 없이 결정론(오라클·대역), recorded = 실호출 녹음 재생")
    s.add_argument("--variant", default="", help="recorded 모드의 변형(예: haiku, sonnet)")
    s.add_argument("--scenario", default="S-B05")
    s.add_argument("--product", default="P-A05")
    s.add_argument("--out", default="")
    s.add_argument("--print", action="store_true", help="사람이 읽는 전사를 출력")
    s.set_defaults(fn=cmd_demo)
    s = sub.add_parser("prereg", help="사전등록 절의 해시를 등록(--register)하거나 그대로인지 검사")
    s.add_argument("--register", help="등록할 문서 경로(예: docs/EVAL_GRAY.md)")
    s.add_argument("--start", default="## 1.", help="사전등록 시작 제목의 앞부분")
    s.add_argument("--end", default="## 5.", help="사전등록 끝 제목의 앞부분(그 앞까지)")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_prereg)
    s = sub.add_parser("agreement-multi", help="독립 라벨러 여럿과 작성자 정답의 일치도(Fleiss κ, 분쟁)")
    s.add_argument("--gold", default="data/gray.jsonl")
    s.add_argument("--labels", default="data/labeler_gray.jsonl")
    s.add_argument("--notes", default="data/gray_notes.jsonl")
    s.add_argument("--out", default="artifacts/agreement_gray.json")
    s.set_defaults(fn=cmd_agreement_multi)

    s = sub.add_parser("validate", help="평가셋 파일의 형식과 라벨 일관성 검사")
    s.add_argument("file")
    s.add_argument("--drop-invalid", action="store_true", help="통과한 줄만 --out 에 쓴다(내용은 고치지 않는다)")
    s.add_argument("--out", default="")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("docs", help="산출물에서 문서의 수치와 결과 문서를 생성(--check 는 불일치만 검사)")
    s.add_argument("--root", default=".")
    s.add_argument("--check", action="store_true")
    s.set_defaults(fn=cmd_docs)

    s = sub.add_parser("judge-check", help="LLM judge 를 한 건 실제로 호출해 확인(키 필요, 비용 발생)")
    s.add_argument("--max-usd", type=float, default=0.5)
    s.add_argument("--model", default="")
    s.add_argument("--timeout", type=float, default=60.0, help="호출 시간 제한(초). 생각하는 모델은 20초를 넘길 수 있다")
    s.set_defaults(fn=cmd_judge_check)

    s = sub.add_parser("judge-record", help="Judge 를 소재마다 실제로 호출해 응답을 녹음(키 필요, 비용 발생). 이어하기 지원")
    s.add_argument("--datasets", default="dev,test,redteam")
    s.add_argument("--data-dir", default="data")
    s.add_argument("--out", default="data/judge_live/recording.jsonl")
    s.add_argument("--log", default="data/judge_live/calls.jsonl")
    s.add_argument("--max-usd", type=float, default=12.0)
    s.add_argument("--model", default="")
    s.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    s.add_argument("--timeout", type=float, default=60.0)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--limit", type=int, default=0, help="앞에서 N건만(0 이면 전부)")
    s.add_argument("--dry-run", action="store_true", help="호출하지 않고 추정 비용만 낸다")
    s.add_argument("--retry-technical", action="store_true", help="이어하기: 기록된 기술적 실패(시간 초과·연결·속도 제한·5xx)만 다시 부른다")
    s.set_defaults(fn=cmd_judge_record)

    s = sub.add_parser("judge-compare", help="두 녹음(대역 대 실호출)을 소재별로 견준다")
    s.add_argument("--a", default="data/judge_replay/recording.jsonl", help="기준(대역)")
    s.add_argument("--b", default="data/judge_live/recording.jsonl", help="견줄 것(실호출)")
    s.add_argument("--out", default="")
    s.set_defaults(fn=cmd_judge_compare)

    s = sub.add_parser("judge-export", help="Judge 프롬프트 내보내기(녹음용)")
    s.add_argument("--datasets", default="dev,test,redteam")
    s.add_argument("--data-dir", default="data")
    s.add_argument("--out-dir", required=True)
    s.add_argument("--shards", type=int, default=9)
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(fn=cmd_judge_export)

    from .catalog.cli import register as register_catalog

    register_catalog(sub)

    s = sub.add_parser("serve", help="API 서버")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
