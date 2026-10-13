"""Judge 호출의 장애 격리: 시간 제한 + 동시 호출 상한(벌크헤드) + 회로 차단기.

LLM 호출은 느려지거나 실패할 수 있고, 그것이 심사 API 전체의 지연으로 번지면 안 된다. 이 래퍼는 Judge 를 감싸서

* 호출 하나가 ``timeout_s`` 안에 끝나지 않으면 기다리기를 포기한다(포기한 호출은 백그라운드에서 끝나게 둔다).
* 동시에 진행 중인 호출이 ``max_concurrent`` 를 넘으면 기다리지 않고 바로 실패시킨다(벌크헤드: 느린 judge 가 스레드를 다 먹지 못하게).
* 연속 ``failure_threshold`` 번 실패하면 회로를 연다 — ``reset_s`` 동안은 호출하지 않고 즉시 실패시킨다. 그 뒤 한 번만 시험 호출(half-open)해서 성공하면 닫는다.

모든 실패는 ``JudgeError`` 로 올라가고 파이프라인이 fail-closed(REVIEW, ``judge_unavailable``)로 처리한다 — 결정론 경로는 영향을 받지 않는다.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from ..judge import Judge, ProposedFinding
from ..judge_anthropic import JudgeError
from ..models import Creative
from ..policy import Policy


class CircuitOpen(JudgeError):
    """회로가 열려 있어 호출하지 않았다."""


class BulkheadFull(JudgeError):
    """진행 중인 judge 호출이 상한에 닿아 새 호출을 받지 않았다."""


class JudgeTimeout(JudgeError):
    """judge 호출이 시간 제한 안에 끝나지 않았다."""


class GuardedJudge:
    def __init__(
        self, inner: Judge, *, timeout_s: float = 8.0, max_concurrent: int = 8, failure_threshold: int = 5, reset_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_s <= 0 or max_concurrent < 1 or failure_threshold < 1 or reset_s <= 0:
            raise ValueError("timeout_s·reset_s 는 양수, max_concurrent·failure_threshold 는 1 이상이어야 합니다")
        self.inner, self.timeout_s, self.max_concurrent = inner, timeout_s, max_concurrent
        self.failure_threshold, self.reset_s, self.clock = failure_threshold, reset_s, clock
        self.name = f"guarded:{getattr(inner, 'name', 'judge')}"
        self.include_absence_context = getattr(inner, "include_absence_context", False)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="judge-guard")
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False
        self.counts: Counter[str] = Counter()

    # -- 상태 ---------------------------------------------------------------------------------------------------------------------
    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "half_open" if self.clock() - self._opened_at >= self.reset_s else "open"

    def _admit(self) -> bool:
        """호출해도 되는가. half-open 에서는 시험 호출 하나만 통과시킨다. 반환값은 이 호출이 시험(probe)인가."""
        with self._lock:
            if self._opened_at is None:
                return False
            if self.clock() - self._opened_at < self.reset_s or self._probing:
                self.counts["short_circuit"] += 1
                raise CircuitOpen("회로가 열려 있습니다")
            self._probing = True
            return True

    def _record(self, ok: bool, probe: bool) -> None:
        with self._lock:
            if probe:
                self._probing = False
            if ok:
                self._failures = 0
                self._opened_at = None
                return
            self._failures += 1
            if probe or self._failures >= self.failure_threshold:
                self._opened_at = self.clock()

    # -- 호출 ---------------------------------------------------------------------------------------------------------------------
    def propose(self, creative: Creative, policy: Policy, already: Sequence[dict[str, Any]] = ()) -> list[ProposedFinding]:
        probe = self._admit()
        if not self._slots.acquire(blocking=False):
            self.counts["bulkhead_full"] += 1
            self._record(False, probe)
            raise BulkheadFull("진행 중인 judge 호출이 상한에 닿았습니다")

        def call() -> list[ProposedFinding]:
            try:
                return list(self.inner.propose(creative, policy, already))
            finally:
                self._slots.release()

        future = self._pool.submit(call)
        try:
            out = future.result(timeout=self.timeout_s)
        except concurrent.futures.TimeoutError as exc:
            self.counts["timeout"] += 1
            self._record(False, probe)
            raise JudgeTimeout(f"{self.timeout_s}초 안에 끝나지 않았습니다") from exc
        except Exception:
            self.counts["error"] += 1
            self._record(False, probe)
            raise
        self.counts["ok"] += 1
        self._record(True, probe)
        return out

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
