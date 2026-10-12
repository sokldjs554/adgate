"""녹음된 Judge 응답을 재생하는 Judge. 키 없이 'LLM 이 더하는 것'을 파이프라인 안에서 재 보기 위한 도구다.

* ``export_prompts`` 가 소재마다 어댑터가 실제로 보낼 사용자 메시지(``user_content``: 소재 + ``already_found``)를 만든다.
  시스템 프롬프트와 출력 스키마도 어댑터의 것 그대로다(``judge_anthropic.system_prompt`` / ``build_schema``).
* 그 프롬프트에 대한 모델 응답(``{"findings": [...]}``)을 파일에 녹음해 두면, ``RecordedJudge`` 가 그 응답을 돌려준다.
  응답은 어댑터와 **같은 검증**(``_Output``: 추가 키 거부)을 통과해야 하고, 이후 ``ground`` 의 근거 검증, 병합, 판정은 실제 파이프라인이다.
* 녹음이 없으면 예외를 던진다 → 파이프라인이 REVIEW 로 보낸다(fail-closed). 조용히 빈 응답으로 취급하지 않는다.

한계(결과 문서에도 적는다): 녹음을 만든 모델·호출 방식이 운영 경로(API 구조화 출력, 지정 모델, 노력 수준)와 같다는 보장은 없다.
이것은 '프롬프트·스키마·근거 검증·병합 규칙이 실제 LLM 출력과 맞물려 어떻게 움직이는가'를 재는 것이지, 어댑터의 API 호출을 재는 것이 아니다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .judge import ProposedFinding
from .judge_anthropic import (
    JudgeError,
    JudgeProtocolError,
    _Output,
    build_schema,
    system_prompt,
    user_content,
)
from .models import Creative
from .policy import Policy


def creative_key(creative: Creative) -> str:
    """소재 내용으로 정한 열쇠(id 가 겹쳐도 안전하다)."""
    return hashlib.sha256(creative.model_dump_json().encode("utf-8")).hexdigest()[:16]


class CapturingJudge:
    """호출 인자만 기록하고 아무것도 제안하지 않는다. 어댑터가 보낼 ``already_found`` 를 얻는 데 쓴다."""

    name = "capture"

    def __init__(self) -> None:
        self.already: list[dict[str, Any]] = []

    def propose(self, creative: Creative, policy: Policy, already: Sequence[dict[str, Any]] = ()) -> Sequence[ProposedFinding]:
        self.already = list(already)
        return []


class RecordedJudge:
    name = "recorded"

    def __init__(self, responses: Mapping[str, Any], errors: Mapping[str, str] | None = None) -> None:
        self.responses = dict(responses)
        self.errors = dict(errors or {})  # 실호출이 실패한 소재 → 실패 종류. 재생하면 같은 소재에서 같이 실패한다(fail-closed)
        self.calls = 0
        self.misses = 0

    @classmethod
    def from_file(cls, path: str | Path) -> RecordedJudge:
        """행은 ``{"key", "response"}``(성공) 또는 ``{"key", "error"}``(실호출 실패)."""
        rows = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
        keys = [r["key"] for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("녹음 파일에 같은 열쇠가 두 번 있습니다")
        return cls({r["key"]: r["response"] for r in rows if "response" in r}, {r["key"]: str(r["error"]) for r in rows if "error" in r})

    def has(self, creative: Creative) -> bool:
        key = creative_key(creative)
        return key in self.responses or key in self.errors

    def propose(self, creative: Creative, policy: Policy, already: Sequence[dict[str, Any]] = ()) -> list[ProposedFinding]:
        self.calls += 1
        key = creative_key(creative)
        if key in self.errors:
            raise JudgeError(f"녹음된 호출이 실패했다: {self.errors[key]}")
        rec = self.responses.get(key)
        if rec is None:
            self.misses += 1
            raise JudgeError("이 소재의 녹음된 응답이 없습니다")
        try:
            return _Output.model_validate(rec).findings
        except ValidationError as exc:
            raise JudgeProtocolError(f"녹음된 응답이 스키마와 다릅니다: {type(exc).__name__}") from exc


def export_prompts(creatives: Sequence[Creative], policy: Policy, cfg_factory: Any) -> list[dict[str, str]]:
    """소재마다 ``{"key", "user"}``. ``cfg_factory(judge)`` 는 그 Judge 를 꽂은 ReviewConfig 를 만든다(결정론 경로는 실제 설정 그대로)."""
    from .pipeline import review

    out: list[dict[str, str]] = []
    for c in creatives:
        cap = CapturingJudge()
        review(c, cfg_factory(cap), policy)
        out.append({"key": creative_key(c), "user": user_content(c, cap.already)})
    return out


def prompt_bundle(policy: Policy) -> dict[str, str]:
    """모델에게 보여 줄 고정 부분: 시스템 프롬프트와 출력 스키마."""
    return {
        "system.txt": system_prompt(policy),
        "schema.json": json.dumps(build_schema(policy), ensure_ascii=False, indent=2),
    }


# 기술적 실패(일시적이라 다시 불러도 되는 것). 거절·잘림·형식 오류는 모델의 응답이므로 다시 부르지 않는다(docs/JUDGE_LIVE.md D2).
TECHNICAL_ERRORS = frozenset({"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError", "OverloadedError"})


def record_live(
    creatives: Mapping[str, Creative], judge: Any, policy: Policy, *, out: Path, log: Path, workers: int = 4,
    limit: int | None = None, cfg_factory: Any = None, say: Any = print, retry_technical: bool = False,
) -> dict[str, Any]:
    """Judge 를 소재마다 한 번씩 실제로 호출해 응답을 ``out`` 에, 호출별 기록을 ``log`` 에 남긴다. 이미 녹음된 소재는 다시 부르지 않는다(이어하기).

    * 성공은 ``{"key", "response"}``, 실패(거절·잘림·형식 오류·시간 초과·예산)는 ``{"key", "error"}`` 로 남긴다.
      ``RecordedJudge`` 가 실패도 재생하므로 라이브 측정과 같은 fail-closed 동작이 재현된다(실패한 소재가 '판정 없음'으로 사라지지 않는다).
    * ``already_found`` 는 파이프라인이 실제로 Judge 에게 넘기는 것과 같다(``CapturingJudge``).
    * ``retry_technical`` 이면 기술적 실패 행(``TECHNICAL_ERRORS``)만 지우고 다시 부른다. 성공한 응답과 모델이 낸 거절·형식 오류는 그대로 둔다.
    * 예산 상한에 닿으면 새 호출을 시작하지 않고 멈춘다. 지금까지의 녹음은 보존된다.
    * 녹음 파일은 마지막에 ``creatives`` 의 순서로 다시 쓴다(동시 호출의 완료 순서가 결과에 새지 않게).
    """
    import concurrent.futures
    import threading

    from .config import ReviewConfig
    from .judge_anthropic import BudgetExceeded
    from .pipeline import review

    cfg_factory = cfg_factory or (lambda j: ReviewConfig(judge=j))
    out.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict[str, Any]] = {}
    if out.exists():
        for ln in out.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                row = json.loads(ln)
                done[row["key"]] = row
    retried = 0
    if retry_technical:
        stale = [k for k, r in done.items() if r.get("error") in TECHNICAL_ERRORS]
        for k in stale:
            del done[k]
        retried = len(stale)
    resumed = sum(1 for k in creatives if k in done)
    todo = [k for k in creatives if k not in done]
    if limit:
        todo = todo[:limit]
    lock = threading.Lock()
    stop = threading.Event()
    counts = {"ok": 0, "failed": 0, "skipped_budget": 0}

    def one(key: str) -> None:
        if stop.is_set():
            with lock:
                counts["skipped_budget"] += 1
            return
        creative = creatives[key]
        cap = CapturingJudge()
        review(creative, cfg_factory(cap), policy)
        row: dict[str, Any]
        entry: dict[str, Any] = {"key": key}
        try:
            proposals = judge.propose(creative, policy, cap.already)
            row = {"key": key, "response": {"findings": [p.model_dump() for p in proposals]}}
            entry.update(ok=True, findings=len(proposals))
        except BudgetExceeded as exc:
            stop.set()
            with lock:
                counts["skipped_budget"] += 1
            say(f"예산 상한에 닿아 멈춥니다: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 기록하고 계속한다(재생하면 fail-closed)
            row = {"key": key, "error": type(exc).__name__}
            entry.update(ok=False, error=type(exc).__name__, detail=str(exc)[:200])
        entry.update(getattr(judge, "last_meta", {}) or {})
        with lock:
            done[key] = row
            counts["ok" if entry["ok"] else "failed"] += 1
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            with log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            n = counts["ok"] + counts["failed"]
            if n % 10 == 0:
                say(f"  {n}/{len(todo)} 호출 (실패 {counts['failed']})")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(one, todo))
    ordered = [done[k] for k in creatives if k in done]
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ordered), encoding="utf-8")
    return {"requested": len(creatives), "resumed": resumed, "retried": retried, "recorded": len(ordered), **counts}
