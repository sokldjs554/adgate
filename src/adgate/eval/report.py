"""측정 기록과 결과 문서. 문서의 수치는 산출물 JSON 에서만 채운다(손으로 옮겨 적지 않는다)."""

from __future__ import annotations

import datetime as dt
import platform
import subprocess
from pathlib import Path
from typing import Any


def git_state(root: Path) -> tuple[str, bool | None]:
    """(짧은 해시, 작업트리가 수정됐는가). git 을 쓸 수 없으면 ("unknown", None) — '깨끗'으로 적지 않는다."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True).stdout.strip())
        return sha, dirty
    except Exception:  # noqa: BLE001
        return "unknown", None


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def meta(root: Path, datasets: dict[str, dict[str, Any]], git: tuple[str, bool | None] | None = None) -> dict[str, Any]:
    """git 상태는 측정을 시작할 때 한 번 읽어 넘기는 게 맞다. 끝에서 다시 읽으면 측정 기록(MEASUREMENTS.md)을 덧붙인 것 때문에 '수정됨'으로 보인다."""
    sha, dirty = git if git is not None else git_state(root)
    return {"git_sha": sha, "dirty": dirty, "created": now(), "python": platform.python_version(), "datasets": datasets}


HEADER = """# 측정 기록

`adgate eval --measure` 가 `test`·`redteam` 을 측정할 때마다 한 줄씩 **자동으로** 덧붙입니다. 손으로 고치지 않습니다.
`test` 를 본 뒤 코드를 고쳤다면 그 이후 `test` 수치는 순수 holdout 이 아닙니다([EVAL_PLAN.md](EVAL_PLAN.md) 7절).

| 시각(UTC) | 데이터셋 | 시스템 | git | 작업트리 | N | accuracy | unsafe_approve | false_reject | 메모 |
|---|---|---|---|---|---|---|---|---|---|
"""


def append_measurement(path: Path, dataset: str, system: str, sha: str, dirty: bool | None, m: dict[str, Any], note: str) -> None:
    if not path.exists():
        path.write_text(HEADER, encoding="utf-8")

    def pct(x: dict[str, Any]) -> str:
        return "-" if x["value"] is None else f"{x['value'] * 100:.1f}% ({x['k']}/{x['n']})"

    row = (
        f"| {now()} | {dataset} | {system} | {sha} | {'알 수 없음' if dirty is None else '수정됨' if dirty else '깨끗'} | {m['n']} | {pct(m['accuracy'])} | "
        f"{pct(m['unsafe_approve'])} | {pct(m['false_reject'])} | {note or ''} |\n"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(row)
