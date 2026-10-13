"""Compare per-case deterministic/v1-replay outputs across two source directories.

No model calls. Remove only stats.latency_ms before hashing the complete result.
Usage: python scripts/compare_review_outputs.py --measure --before /tmp/adgate-baseline-audit --out /tmp/comparison.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def worker(root: Path):
    sys.path.insert(0, str(root / 'src'))
    from adgate.config import ReviewConfig
    from adgate.eval.dataset import load
    from adgate.judge_recorded import RecordedJudge
    from adgate.pipeline import review
    out = {}
    for split in ('dev', 'test', 'redteam'):
        examples = load(root / 'data' / f'{split}.jsonl')
        for name, cap in [('full', None), ('full+judge', False), ('full+judge_cap', True)]:
            judge = RecordedJudge.from_file(root / 'data/judge_live/recording.jsonl') if cap is not None else None
            cfg = ReviewConfig(judge=judge, judge_cap_review=cap if cap is not None else True)
            for ex in examples:
                value = review(ex.creative, cfg).model_dump(mode='json')
                value['stats'].pop('latency_ms')
                out[f'{split}/{name}/{ex.id}'] = value
            if judge and judge.misses:
                raise RuntimeError('Missing recordings')
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--worker', type=Path)
    p.add_argument('--before', type=Path)
    p.add_argument('--measure', action='store_true', help='Acknowledge replay of frozen test/redteam data')
    p.add_argument('--after', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--out', type=Path)
    args = p.parse_args()
    if args.worker:
        worker(args.worker)
        return
    if not args.measure:
        p.error('Frozen data replay requires --measure')
    if not args.before or not args.out:
        p.error('--before and --out are required')
    if args.out.exists():
        raise SystemExit('Refusing to overwrite an existing measurement')
    results = []
    for root in (args.before.resolve(), args.after.resolve()):
        env = {**os.environ, 'PYTHONPATH':str(root / 'src')}
        results.append(json.loads(subprocess.check_output([sys.executable, str(Path(__file__).resolve()), '--worker', str(root)], cwd=root, env=env, text=True)))
    before, after = results
    keys = sorted(before.keys() | after.keys())
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    rows = [{'case':key,'before_sha256':digest(before.get(key)),'after_sha256':digest(after.get(key))} for key in keys]
    changed = [{ 'case':k,'before':before.get(k),'after':after.get(k)} for k in keys if before.get(k) != after.get(k)]
    result = {'before_commit':subprocess.check_output(['git','rev-parse','HEAD'], cwd=args.before, text=True).strip(), 'after_head_at_execution':subprocess.check_output(['git','rev-parse','HEAD'], cwd=args.after, text=True).strip(), 'after_dirty':bool(subprocess.check_output(['git','status','--porcelain'], cwd=args.after, text=True).strip()), 'excluded_fields':['stats.latency_ms'], 'api_calls':0,'compared':len(keys),'changed_count':len(changed),'changes':changed,'hashes':rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    ledger = args.after / 'docs/MEASUREMENTS.md'
    with ledger.open('a') as f:
        f.write(f'\nSupplemental per-case replay: {result["before_commit"]} -> {result["after_head_at_execution"]}; compared={len(keys)}, changed={len(changed)}, excluding stats.latency_ms; output={args.out}. Not independent holdout.\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('changes','hashes')}))
    if changed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
