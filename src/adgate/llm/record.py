"""녹음·재생·실호출 기록. 한 *사례*(case: 상품 하나, 시나리오 하나 …)의 처리 전체를 ``driver(payload, client)`` 로 정의하고, 같은 driver 를 세 방식으로 돌린다.

* 재생(``replay_cases``): ``RecordedClient`` 가 녹음된 응답만 돌려준다. 키도 비용도 필요 없고 결과가 결정론적이다. 녹음에 없는 호출은 ``LLMMissing`` 이다.
* 실호출 기록(``record_cases``): ``HybridClient`` 가 녹음에 있으면 재생하고, 없으면 실제 클라이언트를 불러 응답(또는 실패)을 녹음에 덧붙인다.
  그래서 이어하기가 따로 필요 없다 — 중단한 자리에서 다시 돌리면 이미 한 호출은 재생되고 나머지만 실제로 부른다.
* 닫힌 고리(생성 → 심사 → 수리 …)는 이전 응답에 따라 다음 입력이 달라진다. driver 가 결정론적이면 녹음을 재생하는 것만으로 같은 고리가 그대로 재현된다.

실패도 녹음한다(``{"key","error"}``). 재생하면 같은 자리에서 같이 실패하므로 '실패한 사례가 판정 없이 사라지는' 일이 없다.
실제 호출이 던진 예외(시간 초과, 거절 …)는 live 와 replay 가 같은 모양으로 보이도록 ``LLMRecordedFailure`` 로 감싸 올린다.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import (
    TECHNICAL_ERRORS,
    LLMAccountError,
    LLMBudgetExceeded,
    LLMClient,
    LLMMissing,
    LLMRecordedFailure,
    LLMResult,
    LLMTask,
    Part,
    RunStop,
    parts_of,
    task_key,
)

Row = dict[str, Any]


def load_rows(path: str | Path) -> dict[str, Row]:
    """녹음 파일(JSON Lines)을 열쇠 → 행으로. 같은 열쇠가 두 번 있으면 오류다."""
    rows: dict[str, Row] = {}
    p = Path(path)
    if not p.exists():
        return rows
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            row = json.loads(ln)
            if row["key"] in rows:
                raise ValueError("녹음 파일에 같은 열쇠가 두 번 있습니다")
            rows[row["key"]] = row
    return rows


def _replay(row: Row) -> LLMResult:
    if "error" in row:
        raise LLMRecordedFailure(str(row["error"]))
    return LLMResult(dict(row["response"]), {"recorded": True})


class RecordedClient:
    """녹음된 응답만 돌려준다. 없는 호출은 ``LLMMissing`` (조용히 빈 응답으로 취급하지 않는다)."""

    name = "recorded"

    def __init__(self, rows: Mapping[str, Row], *, base_dir: Path | None = None) -> None:
        self.rows = dict(rows)
        self.base_dir = base_dir
        self.calls = self.misses = 0

    @classmethod
    def from_file(cls, path: str | Path, *, base_dir: Path | None = None) -> RecordedClient:
        return cls(load_rows(path), base_dir=base_dir)

    def has(self, task: LLMTask, user: str | Sequence[Part]) -> bool:
        return task_key(task, user, self.base_dir) in self.rows

    def call(self, task: LLMTask, user: str | Sequence[Part]) -> LLMResult:
        self.calls += 1
        row = self.rows.get(task_key(task, user, self.base_dir))
        if row is None:
            self.misses += 1
            raise LLMMissing(f"{task.name}: 이 호출의 녹음이 없습니다")
        return _replay(row)


class ScriptedClient:
    """테스트용. ``handler(task, parts)`` 가 응답 딕셔너리를 돌려주거나 예외를 던진다."""

    name = "scripted"

    def __init__(self, handler: Callable[[LLMTask, list[Part]], dict[str, Any]]) -> None:
        self.handler = handler
        self.calls: list[tuple[str, list[Part]]] = []
        self._lock = threading.Lock()
        self.last_meta: dict[str, Any] = {}

    def call(self, task: LLMTask, user: str | Sequence[Part]) -> LLMResult:
        parts = parts_of(user)
        with self._lock:
            self.calls.append((task.name, parts))
        return LLMResult(self.handler(task, parts), {"scripted": True})


class RecordingStore:
    """열쇠 → 행. 새 행은 파일에 바로 덧붙여(중단돼도 지금까지의 녹음이 남는다) 호출 기록과 함께 쓴다. 스레드 안전."""

    def __init__(self, out: Path | None, log: Path | None, rows: Mapping[str, Row] | None = None) -> None:
        self.rows: dict[str, Row] = dict(rows or {})
        self.out, self.log = out, log
        self.lock = threading.Lock()
        self.stopped = threading.Event()  # 예산 상한에 닿았다: 새 호출을 시작하지 않는다

    def get(self, key: str) -> Row | None:
        with self.lock:
            return self.rows.get(key)

    def put(self, row: Row, entry: Row) -> bool:
        with self.lock:
            if row["key"] in self.rows:
                return False
            self.rows[row["key"]] = row
            if self.out is not None:
                with self.out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.log_entry(entry, locked=True)
            return True

    def log_entry(self, entry: Row, *, locked: bool = False) -> None:
        if self.log is None:
            return
        if locked:
            with self.log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        else:
            with self.lock, self.log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def flush_sorted(self, order: Mapping[str, int]) -> None:
        """동시 호출의 완료 순서가 결과에 새지 않게 (사례 순서, 호출 순서)로 다시 쓴다."""
        if self.out is None:
            return
        with self.lock:
            rows = sorted(self.rows.values(), key=lambda r: (order.get(str(r.get("case")), len(order)), int(r.get("seq", 0)), r["key"]))
            self.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


class HybridClient:
    """녹음에 있으면 재생하고, 없으면 ``live`` 를 불러 녹음한다. ``live`` 가 None 이면 재생 전용이다."""

    name = "hybrid"

    def __init__(self, store: RecordingStore, live: LLMClient | None, case: str, *, base_dir: Path | None = None) -> None:
        self.store, self.live, self.case, self.base_dir = store, live, case, base_dir
        self.seq = self.live_calls = self.replayed = 0

    def call(self, task: LLMTask, user: str | Sequence[Part]) -> LLMResult:
        key = task_key(task, user, self.base_dir)
        self.seq += 1
        row = self.store.get(key)
        if row is not None:
            self.replayed += 1
            return _replay(row)
        if self.live is None:
            raise LLMMissing(f"{task.name}: 이 호출의 녹음이 없습니다")
        if self.store.stopped.is_set():
            raise RunStop(LLMBudgetExceeded("예산 상한에 닿았거나 계정 문제로 멈췄습니다 — 새 호출을 시작하지 않습니다"))
        base = {"key": key, "task": task.name, "case": self.case, "seq": self.seq}
        try:
            result = self.live.call(task, user)
        except LLMBudgetExceeded as exc:
            self.store.stopped.set()
            raise RunStop(exc) from exc       # LLMError 가 아니다: 에이전트·파이프라인의 fail-closed 가 삼키지 못한다(F-33)
        except Exception as exc:
            name = type(exc).__name__
            meta = dict(getattr(self.live, "last_meta", {}) or {})
            self.live_calls += 1
            self.store.put({**base, "error": name}, {**base, "ok": False, "error": name, "detail": str(exc)[:200], **meta})
            raise LLMRecordedFailure(name) from exc
        self.live_calls += 1
        meta = {**result.meta, **dict(getattr(self.live, "last_meta", {}) or {})}
        self.store.put({**base, "response": result.response}, {**base, "ok": True, **meta})
        return result


@dataclass
class CaseOutcome:
    case: str
    result: Any = None
    error: str | None = None  # 사례가 끝까지 가지 못한 이유(예외 이름). 결과가 없는 사례는 지표에서 '실패'로 센다
    misses: int = 0


def replay_cases(
    cases: Sequence[tuple[str, Any]], driver: Callable[[Any, LLMClient], Any], rows: Mapping[str, Row], *, base_dir: Path | None = None,
) -> list[CaseOutcome]:
    """녹음을 재생해 사례마다 결과를 얻는다. 키·비용이 필요 없다. 사례 하나의 실패가 다른 사례를 막지 않는다."""
    out: list[CaseOutcome] = []
    for cid, payload in cases:
        client = RecordedClient(rows, base_dir=base_dir)
        try:
            out.append(CaseOutcome(cid, driver(payload, client), misses=client.misses))
        except Exception as exc:  # noqa: BLE001 — 사례 하나의 실패는 사례의 결과다
            out.append(CaseOutcome(cid, None, type(exc).__name__, client.misses))
    return out


def record_cases(
    cases: Sequence[tuple[str, Any]], driver: Callable[[Any, LLMClient], Any], *, live: LLMClient, out: Path, log: Path, workers: int = 4,
    limit: int | None = None, retry_technical: bool = False, say: Callable[[str], None] = print, base_dir: Path | None = None,
) -> dict[str, Any]:
    """사례마다 ``driver`` 를 실제 클라이언트로 돌려 응답을 ``out`` 에, 호출별 기록을 ``log`` 에 남긴다. 이미 녹음된 호출은 다시 부르지 않는다(이어하기).

    * ``retry_technical`` 이면 기술적 실패 행(``TECHNICAL_ERRORS``)만 지우고 다시 부른다. 성공한 응답과 모델의 거절·형식 오류는 그대로 둔다.
    * 예산 상한에 닿으면 새 호출을 시작하지 않고 멈춘다. 지금까지의 녹음은 보존된다.
    * 사례가 예외로 끝나면(driver 의 버그나 호출 실패의 전파) 호출 기록에 ``case_error`` 로 남기고 다른 사례는 계속한다.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = load_rows(out)
    retried = 0
    if retry_technical:
        stale = [k for k, r in rows.items() if r.get("error") in TECHNICAL_ERRORS]
        for k in stale:
            del rows[k]
        retried = len(stale)
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()), encoding="utf-8")
    store = RecordingStore(out, log, rows)
    order = {cid: i for i, (cid, _) in enumerate(cases)}
    todo = list(cases[:limit] if limit else cases)
    counts: dict[str, int] = {"completed": 0, "case_errors": 0, "skipped_budget": 0, "live_calls": 0, "replayed_calls": 0}
    lock = threading.Lock()

    def one(item: tuple[str, Any]) -> None:
        cid, payload = item
        if store.stopped.is_set():
            with lock:
                counts["skipped_budget"] += 1
            return
        client = HybridClient(store, live, cid, base_dir=base_dir)
        status = "completed"
        try:
            driver(payload, client)
        except RunStop as stop:
            status = "skipped_budget"
            say(f"{'계정 문제로' if isinstance(stop.cause, LLMAccountError) else '예산 상한에 닿아'} 멈춥니다: {stop.cause}")
        except LLMBudgetExceeded as exc:      # 하니스를 거치지 않고 올라온 상한 초과(드라이버가 직접 만든 클라이언트)
            status = "skipped_budget"
            store.stopped.set()
            say(f"{'계정 문제로' if isinstance(exc, LLMAccountError) else '예산 상한에 닿아'} 멈춥니다: {exc}")
        except Exception as exc:  # noqa: BLE001 — 사례 하나의 실패가 전체를 멈추지 않는다
            status = "case_errors"
            store.log_entry({"case": cid, "case_error": type(exc).__name__, "detail": str(exc)[:200]})
        with lock:
            counts[status] += 1
            counts["live_calls"] += client.live_calls
            counts["replayed_calls"] += client.replayed
            done = counts["completed"] + counts["case_errors"]
            if done and done % 10 == 0:
                say(f"  {done}/{len(todo)} 사례 (실제 호출 {counts['live_calls']})")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(one, todo))
    store.flush_sorted(order)
    return {"requested": len(cases), "attempted": len(todo), "retried": retried, "recorded_rows": len(store.rows), **counts}
