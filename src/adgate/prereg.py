"""사전등록 문서의 본문 해시 등록부. 측정 *전에* 쓴 절(예상·판단 규칙)을 측정 뒤에 고치지 못하게 한다.

``docs/PREREGISTRATIONS.json`` 에 (문서, 시작 제목, 끝 제목, 본문 sha256) 을 둔다. ``adgate prereg --check`` 와 ``tests/test_prereg.py`` 가
본문이 그대로인지 검사한다. 결과와 '빗나감'은 끝 제목 *뒤*에 덧붙인다. 등록은 측정(실호출·첫 접촉) 전에 커밋한다 — 순서는 git 기록이 증명한다.
JUDGE_REPLAY/JUDGE_LIVE 는 이 등록부가 생기기 전에 같은 방식(테스트 속 해시)으로 고정했고 그대로 둔다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REGISTRY = Path("docs/PREREGISTRATIONS.json")


def section_body(text: str, start: str, end: str | None) -> str:
    """``start`` 로 시작하는 제목 줄부터 ``end`` 로 시작하는 제목 줄 *앞*까지. ``end`` 가 None 이면 끝까지."""
    lines = text.splitlines()
    try:
        a = next(i for i, ln in enumerate(lines) if ln.startswith(start))
    except StopIteration as exc:
        raise ValueError(f"시작 제목 {start!r} 를 찾지 못했다") from exc
    b = len(lines)
    if end is not None:
        try:
            b = next(i for i, ln in enumerate(lines) if i > a and ln.startswith(end))
        except StopIteration as exc:
            raise ValueError(f"끝 제목 {end!r} 를 찾지 못했다") from exc
    return "\n".join(lines[a:b]).strip("\n")


def digest(text: str, start: str, end: str | None) -> str:
    return hashlib.sha256(section_body(text, start, end).encode("utf-8")).hexdigest()


def load(root: Path = Path(".")) -> list[dict[str, Any]]:
    p = root / REGISTRY
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def register(doc: str, start: str, end: str | None, *, root: Path = Path("."), note: str = "") -> dict[str, Any]:
    """문서의 사전등록 절을 등록부에 올린다. 같은 문서·구간이 이미 있으면 거부한다(고쳐 쓰기 위한 재등록은 없다)."""
    entries = load(root)
    if any(e["doc"] == doc and e["start"] == start for e in entries):
        raise ValueError(f"{doc} {start!r} 는 이미 등록돼 있다 — 사전등록은 다시 등록하지 않는다")
    text = (root / doc).read_text(encoding="utf-8")
    entry = {"doc": doc, "start": start, "end": end, "sha256": digest(text, start, end), "registered": datetime.now(UTC).strftime("%Y-%m-%d"), "note": note}
    (root / REGISTRY).write_text(json.dumps([*entries, entry], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entry


def check(root: Path = Path(".")) -> list[str]:
    """등록된 절이 그대로인지. 어긋난 항목의 설명 목록(비어 있어야 한다)."""
    errs: list[str] = []
    for e in load(root):
        try:
            text = (root / e["doc"]).read_text(encoding="utf-8")
            got = digest(text, e["start"], e["end"])
        except (OSError, ValueError) as exc:
            errs.append(f"{e['doc']}: {exc}")
            continue
        if got != e["sha256"]:
            errs.append(f"{e['doc']} {e['start']!r}~{e['end']!r}: 사전등록 본문이 등록 뒤에 바뀌었다")
    return errs
