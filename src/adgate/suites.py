"""LLM 측정 묶음(suite). 사례 목록 + 사례 하나를 처리하는 driver + 채점을 한 번 정의하면 세 곳이 같이 쓴다.

* ``dry-run`` — 호출하지 않고(대역 클라이언트로 닫힌 고리를 끝까지 돌려) 호출 수와 *보수적* 추정 비용을 낸다. 사용자 승인의 근거다.
* ``record`` — 실제 호출로 응답을 ``data/llm/<suite>/<variant>.jsonl`` 에 녹음한다(키 필요, 비용 발생, 이어하기, 상한). GitHub Actions ``llm-live`` 가 쓴다.
* ``replay`` — 녹음만으로 채점한다. 키·비용이 필요 없고 결정론이다 → ``artifacts/results_<suite>__<variant>.json``.

변형(variant)은 모델 이름 하나다. 프롬프트·스키마·입력이 바뀌면 열쇠가 바뀌어 낡은 녹음은 재생되지 않는다(없는 열쇠는 오류다).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .llm import CaseOutcome, LLMClient, LLMResult, LLMTask, load_rows, record_cases, replay_cases
from .llm.core import Part, parts_of
from .llm.spend import Spend

Driver = Callable[[Any, LLMClient], Any]
IMAGE_CHARS = 1600


@dataclass(frozen=True)
class Variant:
    name: str
    model: str
    note: str = ""


@dataclass
class Suite:
    name: str
    describe: str
    prereg: str
    variants: dict[str, Variant]
    cases: Callable[[str], list[tuple[str, Any]]]              # 변형 → 사례 (변형마다 사례 부분집합이 다를 수 있다)
    driver: Callable[[str], Driver]
    standin: Callable[[Any], LLMClient]                        # 사례 → dry-run 용 대역(유효한 응답을 만들어 닫힌 고리를 끝까지 돌린다)
    score: Callable[[str, list[CaseOutcome]], dict[str, Any]]
    offline: Callable[[], dict[str, Any]] | None = None       # 모델 없는 팔(규칙 추출기·오라클 …)
    check_case: Callable[[], tuple[str, Any]] | None = None
    tags: list[str] = field(default_factory=list)


REGISTRY: dict[str, Callable[[], Suite]] = {}


def register(name: str) -> Callable[[Callable[[], Suite]], Callable[[], Suite]]:
    def deco(fn: Callable[[], Suite]) -> Callable[[], Suite]:
        REGISTRY[name] = fn
        return fn

    return deco


def get(name: str) -> Suite:
    _load_all()
    if name not in REGISTRY:
        raise SystemExit(f"알 수 없는 suite: {name} (가능: {', '.join(sorted(REGISTRY))})")
    return REGISTRY[name]()


def names() -> list[str]:
    _load_all()
    return sorted(REGISTRY)


SUITE_MODULES = ("adgate.campaign.suite",)


def _load_all() -> None:
    for m in SUITE_MODULES:           # 임포트의 부수 효과로 register 가 불린다
        importlib.import_module(m)


def paths(suite: str, variant: str, root: Path = Path(".")) -> tuple[Path, Path]:
    d = root / "data" / "llm" / suite
    return d / f"{variant}.jsonl", d / f"{variant}.calls.jsonl"


# --------------------------------------------------------------------------- dry-run
class DryClient:
    """대역이 만든 응답을 돌려주며 호출 수와 보수적 추정 비용(입력 글자당 토큰 1개, 출력은 과제의 기대치)을 센다."""

    name = "dry"

    def __init__(self, inner: LLMClient, model: str) -> None:
        self.inner, self.spend = inner, Spend(model, None)
        self.calls = 0
        self.est_usd = 0.0
        self.by_task: dict[str, list[float]] = {}

    def call(self, task: LLMTask, user: str | Sequence[Part]) -> LLMResult:
        chars = len(task.system) + sum(len(p) if isinstance(p, str) else IMAGE_CHARS for p in parts_of(user))
        usd = self.spend.estimate(chars, expected_output_tokens=task.expected_output_tokens)
        self.calls += 1
        self.est_usd += usd
        row = self.by_task.setdefault(task.name, [0, 0.0])
        row[0] += 1
        row[1] += usd
        return self.inner.call(task, user)


def dry_run(suite: Suite, variant: str) -> dict[str, Any]:
    v = suite.variants[variant]
    cases = suite.cases(variant)
    driver = suite.driver(variant)
    calls, usd = 0, 0.0
    by_task: dict[str, list[float]] = {}
    failed: list[str] = []
    for cid, payload in cases:
        dc = DryClient(suite.standin(payload), v.model)
        try:
            driver(payload, dc)
        except Exception as exc:  # noqa: BLE001 — 대역이 끝까지 못 돌린 사례는 추정에서 빠진다(알린다)
            failed.append(f"{cid}:{type(exc).__name__}")
        calls += dc.calls
        usd += dc.est_usd
        for k, (n, c) in dc.by_task.items():
            row = by_task.setdefault(k, [0, 0.0])
            row[0] += n
            row[1] += c
    return {
        "suite": suite.name, "variant": variant, "model": v.model, "cases": len(cases), "calls": calls, "estimated_usd_upper_bound": round(usd, 4),
        "by_task": {k: {"calls": int(n), "usd": round(c, 4)} for k, (n, c) in sorted(by_task.items())}, "stand_in_failed": failed,
        "note": "입력은 글자당 토큰 1개로 본 보수적 상한이다(judge-live 에서 실제 비용은 추정의 약 22% 였다). 대역이 만든 응답으로 닫힌 고리를 끝까지 돌려 호출 수를 센다 — 실제 모델이 다르게 답하면 호출 수가 조금 달라질 수 있다.",
    }


# --------------------------------------------------------------------------- record / replay
def record(suite: Suite, variant: str, *, max_usd: float, workers: int = 4, retry_technical: bool = False, root: Path = Path("."),
           say: Callable[[str], None] = print) -> dict[str, Any]:
    from .llm.anthropic_client import AnthropicClient

    v = suite.variants[variant]
    out, log = paths(suite.name, variant, root)
    client = AnthropicClient(model=v.model, max_usd=max_usd)
    summary = record_cases(suite.cases(variant), suite.driver(variant), live=client, out=out, log=log, workers=workers, retry_technical=retry_technical, say=say)
    summary["spend"] = client.spend.summary()
    return summary


def _git_state() -> tuple[str, bool]:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip())
        return sha, dirty
    except Exception:  # noqa: BLE001
        return "unknown", True


def replay(suite: Suite, variant: str, *, root: Path = Path(".")) -> dict[str, Any]:
    rows_path, _ = paths(suite.name, variant, root)
    if not rows_path.exists():
        raise SystemExit(f"녹음이 없습니다: {rows_path}")
    rows = load_rows(rows_path)
    outcomes = replay_cases(suite.cases(variant), suite.driver(variant), rows)
    sha, dirty = _git_state()
    return {
        "meta": {
            "suite": suite.name, "variant": variant, "model": suite.variants[variant].model, "git_sha": sha, "dirty": dirty,
            "recording_sha256": hashlib.sha256(rows_path.read_bytes()).hexdigest(), "recorded_rows": len(rows),
            "cases": len(outcomes), "case_errors": {o.case: o.error for o in outcomes if o.error}, "misses": sum(o.misses for o in outcomes),
            "prereg": suite.prereg,
        },
        "results": suite.score(variant, outcomes),
    }


def check(suite: Suite, variant: str, *, max_usd: float = 0.5) -> dict[str, Any]:
    """한 사례를 실제로 불러 요청이 받아들여지는지만 본다(측정에 넣지 않는다 — 임시 파일에 녹음한다)."""
    from .llm.anthropic_client import AnthropicClient

    if suite.check_case is None:
        cid, payload = suite.cases(variant)[0]
    else:
        cid, payload = suite.check_case()
    client = AnthropicClient(model=suite.variants[variant].model, max_usd=max_usd)
    with tempfile.TemporaryDirectory() as d:
        summary = record_cases([(cid, payload)], suite.driver(variant), live=client, out=Path(d) / "r.jsonl", log=Path(d) / "c.jsonl", workers=1)
    summary["spend"] = client.spend.summary()
    return summary


def dump(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
