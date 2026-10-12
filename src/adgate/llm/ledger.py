"""녹음 호출 기록(``data/llm/<suite>/<변형>.calls.jsonl``)에서 *실제로 쓴* 토큰·추정 비용·지연을 모은 장부.

문서의 비용 수치는 이 장부(``artifacts/llm_spend.json``)에서만 가져온다. 기록은 실호출 때 함께 남는 행(호출 하나당 한 줄)이고,
비용은 ``Spend`` 와 같은 가격표·같은 공식(캐시 쓰기 1.25배, 캐시 읽기 0.1배)으로 *추정*한다 — 청구서가 아니다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .spend import cost_usd

DEFAULT_ROOT = Path("data") / "llm"


def _percentile(sorted_values: list[int], q: float) -> int:
    """최근접 순위 백분위(표본이 작아 보간하지 않는다)."""
    if not sorted_values:
        return 0
    idx = min(len(sorted_values) - 1, max(0, round(q * len(sorted_values) + 0.5) - 1))
    return sorted_values[idx]


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """호출 기록 행들을 한 변형의 요약으로. 응답을 못 받은 행(``ok`` 가 거짓)은 따로 세고 토큰은 있는 만큼만 더한다."""
    n = ok = 0
    inp = out = cw = cr = 0
    lat: list[int] = []
    cases: set[str] = set()
    models: set[str] = set()
    for r in rows:
        n += 1
        ok += bool(r.get("ok"))
        inp += int(r.get("input_tokens") or 0)
        out += int(r.get("output_tokens") or 0)
        cw += int(r.get("cache_write_tokens") or 0)
        cr += int(r.get("cache_read_tokens") or 0)
        if r.get("latency_ms") is not None:
            lat.append(int(r["latency_ms"]))
        cases.add(str(r.get("case", "")))
        models.add(str(r.get("model", "")))
    lat.sort()
    model = sorted(models)[0] if len(models) == 1 else ",".join(sorted(models))
    usd = cost_usd(model, inp, out, cw, cr) if len(models) == 1 else None
    return {
        "model": model, "calls": n, "ok": ok, "failed": n - ok, "cases": len(cases - {""}),
        "input_tokens": inp, "output_tokens": out, "cache_write_tokens": cw, "cache_read_tokens": cr,
        "usd": None if usd is None else round(usd, 6),
        "usd_per_call": None if usd is None or not n else round(usd / n, 6),
        "latency_ms": {"p50": _percentile(lat, 0.5), "p95": _percentile(lat, 0.95), "max": lat[-1] if lat else 0},
    }


def build(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    """``root`` 아래 모든 ``*.calls.jsonl`` 을 읽어 ``{suite: {변형: 요약}}`` 과 합계를 만든다. 파일이 없으면 빈 장부다."""
    out: dict[str, Any] = {}
    total_usd = 0.0
    total_calls = 0
    for path in sorted(Path(root).glob("*/*.calls.jsonl")):
        suite, variant = path.parent.name, path.name.removesuffix(".calls.jsonl")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        s = summarize(rows)
        out.setdefault(suite, {})[variant] = s
        total_usd += s["usd"] or 0.0
        total_calls += s["calls"]
    return {
        "meta": {
            "note": "녹음 호출 기록에서 모은 *추정* 비용이다(청구서가 아니다). 가격표와 공식은 adgate.llm.spend.",
            "source": "data/llm/<suite>/<variant>.calls.jsonl",
        },
        "total": {"calls": total_calls, "usd": round(total_usd, 6)},
        "suites": out,
    }
