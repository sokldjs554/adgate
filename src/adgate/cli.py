"""명령줄 도구. 심사, 평가, 게이트, 데이터 생성, 일치도, 서버."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import ReviewConfig
from .eval import agreement, dataset, gate, report
from .eval import gen as generator
from .eval import metrics as eval_metrics
from .eval.dataset import Example
from .eval.runner import errors as eval_errors
from .eval.runner import run_system
from .eval.systems import ablations, all_to_human, naive_blocklist, pipeline_system
from .models import Creative
from .pipeline import review

ROOT = Path.cwd()
FROZEN = ("test", "redteam")


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


def _verify_frozen(data_dir: Path, names: list[str], allow_modified: bool) -> None:
    manifest = data_dir / "MANIFEST.sha256"
    if not manifest.exists():
        return
    status = dataset.verify_manifest(manifest)
    for name in names:
        fname = f"{name}.jsonl"
        if fname in status and not status[fname] and not allow_modified:
            raise SystemExit(
                f"{fname} 이(가) 동결 시점(MANIFEST.sha256)과 다릅니다. 라벨 오류 수정이면 --allow-modified-frozen 과 메모를 쓰세요."
            )


def cmd_eval(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    names = [n for n in args.datasets.split(",") if n]
    frozen_used = [n for n in names if n in FROZEN]
    if frozen_used and not args.measure:
        raise SystemExit(f"{','.join(frozen_used)} 측정은 --measure 가 필요합니다(측정 기록이 자동으로 남습니다).")
    _verify_frozen(data_dir, frozen_used, args.allow_modified_frozen)

    systems: dict[str, Any] = {"B0_all_human": all_to_human(), "B1_blocklist": naive_blocklist(), **ablations()}
    if args.systems:
        wanted = set(args.systems.split(","))
        systems = {k: v for k, v in systems.items() if k in wanted}

    results: dict[str, dict[str, Any]] = {}
    ds_meta: dict[str, dict[str, Any]] = {}
    error_dump: dict[str, Any] = {}
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
        for sname, system in systems.items():
            preds = run_system(system, examples)
            results[name][sname] = eval_metrics.compute(examples, preds)
            if sname == "full":
                error_dump[name] = eval_errors(examples, preds)
        if name in FROZEN and "full" in results[name]:
            report.append_measurement(Path(args.measurements), name, "full", sha, dirty, results[name]["full"], args.note)

    out = {"meta": report.meta(ROOT, ds_meta), "results": results}
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
    data_dir = Path(args.data_dir)
    _verify_frozen(data_dir, ["test"], args.allow_modified_frozen)
    datasets = {n: dataset.load(_path(n, data_dir)) for n in ("dev", "test")}
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


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

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

    s = sub.add_parser("serve", help="API 서버")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
