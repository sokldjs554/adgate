"""명령줄 도구. 심사, 평가, 게이트, 데이터 생성, 일치도, 서버."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import ReviewConfig
from .eval import agreement, dataset, gate, judge_replay, report
from .eval import gen as generator
from .eval import metrics as eval_metrics
from .eval.dataset import Example
from .eval.runner import errors as eval_errors
from .eval.runner import run_system
from .eval.systems import Prediction, ablations, all_to_human, naive_blocklist, pipeline_system
from .judge_recorded import RecordedJudge, creative_key, export_prompts, prompt_bundle
from .models import Creative
from .pipeline import review
from .policy import load_policy

ROOT = Path.cwd()
FROZEN = ("test", "redteam")
DATASETS = ("dev", "test", "redteam", "generated")  # 알려진 이름만 받는다('./test' 같은 경로로 --measure 가드를 피하지 못하게)
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
        systems["full+judge"] = pipeline_system(ReviewConfig(judge=judge_obj))
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
                ds_systems["full+judge"] = pipeline_system(ReviewConfig(judge=recorded))
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

    judge = AnthropicJudge(max_usd=args.max_usd, model=args.model or None)
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
    print("사용량:", json.dumps(judge.spend.summary(), ensure_ascii=False))
    return 0 if not result.stats.judge_failed else 1


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
        help="LLM judge 를 켠 구성('full+judge')을 함께 측정한다. anthropic: 실호출(키 필요, 비용 발생). recorded: --judge-file 의 녹음을 재생",
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
    s.set_defaults(fn=cmd_judge_check)

    s = sub.add_parser("judge-export", help="Judge 프롬프트 내보내기(녹음용)")
    s.add_argument("--datasets", default="dev,test,redteam")
    s.add_argument("--data-dir", default="data")
    s.add_argument("--out-dir", required=True)
    s.add_argument("--shards", type=int, default=9)
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(fn=cmd_judge_export)

    s = sub.add_parser("serve", help="API 서버")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
