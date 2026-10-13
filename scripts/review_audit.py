"""Offline audit. Never calls an API; output files cannot be overwritten.

Usage: python scripts/review_audit.py --out artifacts/audit/fresh.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from collections import Counter
from pathlib import Path

from adgate.config import ReviewConfig
from adgate.judge_llm import LLMJudge
from adgate.llm import ScriptedClient
from adgate.models import Creative
from adgate.pipeline import review
from adgate.rewrite import rewrite

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rate(k, n):
    return {'k': k, 'n': n, 'value': k/n if n else None}


def score(rows):
    n = len(rows)
    normal = [r for r in rows if r['gold'] == 'APPROVE']
    risk = [r for r in rows if r['gold'] != 'APPROVE']
    latencies = sorted(r['result']['stats']['latency_ms'] for r in rows)
    return {
        'accuracy': rate(sum(r['gold'] == r['result']['outcome'] for r in rows), n),
        'unsafe_approve': rate(sum(r['result']['outcome'] == 'APPROVE' for r in risk), len(risk)),
        'false_reject': rate(sum(r['result']['outcome'] == 'REJECT' for r in normal), len(normal)),
        'human_review': rate(sum(r['result']['outcome'] == 'REVIEW' for r in rows), n),
        'normal_to_review': rate(sum(r['result']['outcome'] == 'REVIEW' for r in normal), len(normal)),
        'cpu_latency_ms': {'p50': latencies[(n-1)//2], 'p95': latencies[min(n-1, int(n*.95))]},
        'errors': [r['id'] for r in rows if r['gold'] != r['result']['outcome']],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        raise SystemExit('Refusing to overwrite an existing measurement')
    source = ROOT / 'data/audit/review_fresh.jsonl'
    expected = (source.parent / 'MANIFEST.sha256').read_text().split()[0]
    if sha(source) != expected:
        raise SystemExit('Frozen data hash mismatch')
    rows = []
    prompt_contract = []
    for line in source.read_text().splitlines():
        row = json.loads(line)
        ad = Creative.model_validate(row['creative'])
        result = review(ad)
        rw = rewrite(ad, result) if result.outcome != 'APPROVE' else None
        rows.append({**row, 'result': result.model_dump(mode='json'),
                     'rewrite': rw.model_dump(mode='json') if rw else None})
        def capture(task, parts):
            prompt_contract.append({'id': row['id'], 'version': task.version,
                                    'system_sha256': hashlib.sha256(task.system.encode()).hexdigest(),
                                    'user': parts[0], 'schema': task.schema})
            return {'findings': []}
        from adgate.policy import load_policy
        # Contract export only. Its empty stub is NOT an LLM performance arm.
        review(ad, ReviewConfig(judge=LLMJudge(ScriptedClient(capture), load_policy(), 'v3')))
    rws = [r['rewrite'] for r in rows if r['rewrite']]
    out = {
        'meta': {'git_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                 'data_sha256': expected, 'python': platform.python_version(),
                 'status': 'new synthetic, code-aware author; not independent holdout',
                 'api_calls': 0, 'api_cost_usd_this_run': 0,
                 'v3_accuracy': None, 'v3_latency': None, 'v3_cost': None,
                 'v3_measurement_status': 'unmeasured: no local key or approved budget; remote last check credit exhausted'},
        'deterministic': score(rows),
        'rewrite_status': dict(Counter(r['status'] for r in rws)),
        'rewrite_problem_ids': [r['id'] for r in rows if r['rewrite'] and r['rewrite']['verification'] and r['rewrite']['verification']['problems']],
        'cases': rows, 'v3_prompt_contract_export_not_model_results': prompt_contract,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in out.items() if k not in ('cases','v3_prompt_contract_export_not_model_results')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
