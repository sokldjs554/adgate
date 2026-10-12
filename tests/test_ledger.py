"""비용 장부(adgate.llm.ledger): 호출 기록 → 변형별 요약. 비용 공식은 ``Spend`` 와 같아야 한다(문서의 비용 수치가 여기서 온다)."""

from __future__ import annotations

import json
from pathlib import Path

from adgate.llm import Spend, cost_usd
from adgate.llm.ledger import build, summarize


def _row(case: str, *, ok: bool = True, inp: int = 1000, out: int = 200, cw: int = 0, cr: int = 0, ms: int = 1000, model: str = "claude-sonnet-5-5") -> dict[str, object]:
    return {"key": f"k-{case}", "task": "t", "case": case, "seq": 1, "ok": ok, "model": model, "latency_ms": ms, "stop_reason": "end_turn",
            "input_tokens": inp, "output_tokens": out, "cache_write_tokens": cw, "cache_read_tokens": cr}


def test_cost_matches_spend_formula() -> None:
    s = Spend("claude-opus-5-5", None)
    s.input_tokens, s.output_tokens, s.cache_write_tokens, s.cache_read_tokens = 1000, 200, 400, 5000
    assert abs(s.usd - cost_usd("claude-opus-5-5", 1000, 200, 400, 5000)) < 1e-12
    # 캐시 쓰기 1.25배·읽기 0.1배: (1000 + 500 + 500) * 4 + 200 * 20 = 12000 → $0.012
    assert abs(cost_usd("claude-opus-5-5", 1000, 200, 400, 5000) - 0.012) < 1e-12


def test_unknown_model_is_priced_at_the_most_expensive_rate() -> None:
    assert cost_usd("some-future-model", 1_000_000, 0) == 10.0


def test_summarize_counts_failures_and_cases() -> None:
    rows = [_row("A", ms=100), _row("A", ms=300), _row("B", ok=False, inp=0, out=0, ms=900), _row("C", ms=200)]
    s = summarize(rows)
    assert (s["calls"], s["ok"], s["failed"], s["cases"]) == (4, 3, 1, 3)
    assert s["input_tokens"] == 3000 and s["output_tokens"] == 600
    assert s["latency_ms"] == {"p50": 200, "p95": 900, "max": 900}
    assert abs(s["usd"] - cost_usd("claude-sonnet-5-5", 3000, 600)) < 1e-6


def test_summarize_mixed_models_has_no_single_price() -> None:
    s = summarize([_row("A", model="claude-sonnet-5-5"), _row("B", model="claude-haiku-5-5")])
    assert s["usd"] is None and s["usd_per_call"] is None and "claude-haiku-5-5" in s["model"]


def test_build_reads_every_calls_file(tmp_path: Path) -> None:
    for suite, variant, model in (("vision", "haiku", "claude-haiku-5-5"), ("vision", "opus", "claude-opus-5-5"), ("creative", "haiku", "claude-haiku-5-5")):
        d = tmp_path / suite
        d.mkdir(exist_ok=True)
        (d / f"{variant}.calls.jsonl").write_text("\n".join(json.dumps(_row(f"c{i}", model=model)) for i in range(3)) + "\n", encoding="utf-8")
        (d / f"{variant}.jsonl").write_text("{}\n", encoding="utf-8")     # 녹음 본체는 장부에 들어가지 않는다
    led = build(tmp_path)
    assert set(led["suites"]) == {"vision", "creative"} and set(led["suites"]["vision"]) == {"haiku", "opus"}
    assert led["total"]["calls"] == 9
    assert abs(led["total"]["usd"] - sum(v["usd"] for s in led["suites"].values() for v in s.values())) < 1e-5


def test_build_on_empty_root_is_an_empty_ledger(tmp_path: Path) -> None:
    led = build(tmp_path / "nothing")
    assert led["suites"] == {} and led["total"] == {"calls": 0, "usd": 0.0}


def test_committed_ledger_matches_recordings() -> None:
    """저장소의 장부(artifacts/llm_spend.json)가 현재 호출 기록과 같아야 한다 — 새로 녹음하고 장부를 안 고치면 CI 가 잡는다."""
    root = Path(__file__).resolve().parents[1]
    committed = json.loads((root / "artifacts" / "llm_spend.json").read_text(encoding="utf-8"))
    assert build(root / "data" / "llm") == committed
