"""SQLite 저장소: 심사 실행(runs)과 추가 전용 이벤트 로그(events).

* 이벤트는 지우거나 고치지 않는다(INSERT 만). 실행의 현재 상태는 runs 행이고, 왜 그렇게 됐는지는 events 가 말해 준다.
* (제출자, Idempotency-Key) 는 유일하다. 같은 키로 동시에 들어와도 한 건만 만들어진다.
* resume 은 ``WHERE state='waiting_input'`` 조건부 UPDATE 로 한 번만 성공한다.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  submitter TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('completed','waiting_input','failed')),
  creative_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  final_outcome TEXT NOT NULL CHECK (final_outcome IN ('APPROVE','REJECT','REVIEW')),
  decided_by TEXT NOT NULL CHECK (decided_by IN ('auto','human','pending')),
  idempotency_key TEXT,
  request_hash TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS runs_idem ON runs(submitter, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS runs_state ON runs(state);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id),
  seq INTEGER NOT NULL,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE (run_id, seq)
);
"""


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class Run:
    id: str
    created_at: str
    submitter: str
    state: str
    creative: dict[str, Any]
    result: dict[str, Any]
    final_outcome: str
    decided_by: str
    idempotency_key: str | None
    request_hash: str | None


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)

    # ---- 쓰기
    def create_run(
        self, *, submitter: str, creative: dict[str, Any], result: dict[str, Any], state: str, final_outcome: str,
        decided_by: str, idempotency_key: str | None, request_hash: str | None, events: list[tuple[str, dict[str, Any]]],
    ) -> Run:
        run_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, now(), submitter, state, json.dumps(creative, ensure_ascii=False),
                     json.dumps(result, ensure_ascii=False), final_outcome, decided_by, idempotency_key, request_hash),
                )
                for seq, (etype, payload) in enumerate(events, start=1):
                    self._db.execute(
                        "INSERT INTO events (run_id, seq, ts, type, payload_json) VALUES (?,?,?,?,?)",
                        (run_id, seq, now(), etype, json.dumps(payload, ensure_ascii=False)),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        run = self.get(run_id)
        assert run is not None
        return run

    def add_event(self, run_id: str, etype: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM events WHERE run_id=?", (run_id,)).fetchone()
                self._db.execute(
                    "INSERT INTO events (run_id, seq, ts, type, payload_json) VALUES (?,?,?,?,?)",
                    (run_id, row["n"], now(), etype, json.dumps(payload, ensure_ascii=False)),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def resume(self, run_id: str, *, verdict: str, reviewer: str, reason: str) -> bool:
        """waiting_input 인 실행을 사람이 결정한다. 이미 결정됐으면 False(조건부 UPDATE, 경쟁에서 한 번만 성공)."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cur = self._db.execute(
                    "UPDATE runs SET state='completed', final_outcome=?, decided_by='human' WHERE id=? AND state='waiting_input'",
                    (verdict, run_id),
                )
                if cur.rowcount != 1:
                    self._db.execute("ROLLBACK")
                    return False
                row = self._db.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM events WHERE run_id=?", (run_id,)).fetchone()
                self._db.execute(
                    "INSERT INTO events (run_id, seq, ts, type, payload_json) VALUES (?,?,?,?,?)",
                    (run_id, row["n"], now(), "resumed",
                     json.dumps({"verdict": verdict, "reviewer": reviewer, "reason": reason}, ensure_ascii=False)),
                )
                self._db.execute("COMMIT")
                return True
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    # ---- 읽기
    @staticmethod
    def _run(row: sqlite3.Row) -> Run:
        return Run(
            id=row["id"], created_at=row["created_at"], submitter=row["submitter"], state=row["state"],
            creative=json.loads(row["creative_json"]), result=json.loads(row["result_json"]),
            final_outcome=row["final_outcome"], decided_by=row["decided_by"],
            idempotency_key=row["idempotency_key"], request_hash=row["request_hash"],
        )

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run(row) if row else None

    def find_idempotent(self, submitter: str, key: str) -> Run | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE submitter=? AND idempotency_key=?", (submitter, key)).fetchone()
        return self._run(row) if row else None

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT seq, ts, type, payload_json FROM events WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        return [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], "payload": json.loads(r["payload_json"])} for r in rows]

    def parent_run_id(self, run_id: str) -> str | None:
        # The link is inserted atomically with the child run; old databases need no migration.
        with self._lock:
            row = self._db.execute("SELECT payload_json FROM events WHERE run_id=? AND seq=1", (run_id,)).fetchone()
        return json.loads(row["payload_json"]).get("parent_run_id") if row else None

    def list_runs(
        self, *, submitter: str | None = None, state: str | None = None, limit: int = 50, offset: int = 0, oldest_first: bool = False,
    ) -> list[Run]:
        sql = "SELECT * FROM runs"
        args: list[str | int] = []
        where: list[str] = []
        if submitter is not None:
            where.append("submitter=?")
            args.append(submitter)
        if state is not None:
            where.append("state=?")
            args.append(state)
        if where:
            sql += " WHERE " + " AND ".join(where)
        direction = "ASC" if oldest_first else "DESC"
        sql += f" ORDER BY created_at {direction}, id {direction} LIMIT ? OFFSET ?"
        args += [limit, max(0, offset)]
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [self._run(r) for r in rows]

    def count(self, *, submitter: str | None = None, state: str | None = None) -> int:
        sql, args, where = "SELECT COUNT(*) AS n FROM runs", [], []
        if submitter is not None:
            where.append("submitter=?")
            args.append(submitter)
        if state is not None:
            where.append("state=?")
            args.append(state)
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._lock:
            return int(self._db.execute(sql, args).fetchone()["n"])

    def count_by_state(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT state, COUNT(*) AS n FROM runs GROUP BY state").fetchall()
        return {r["state"]: r["n"] for r in rows}

    def close(self) -> None:
        with self._lock:
            self._db.close()
