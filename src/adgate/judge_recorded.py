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

    def __init__(self, responses: Mapping[str, Any]) -> None:
        self.responses = dict(responses)
        self.calls = 0
        self.misses = 0

    @classmethod
    def from_file(cls, path: str | Path) -> RecordedJudge:
        rows = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
        keys = [r["key"] for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("녹음 파일에 같은 열쇠가 두 번 있습니다")
        return cls({r["key"]: r["response"] for r in rows})

    def has(self, creative: Creative) -> bool:
        return creative_key(creative) in self.responses

    def propose(self, creative: Creative, policy: Policy, already: Sequence[dict[str, Any]] = ()) -> list[ProposedFinding]:
        self.calls += 1
        rec = self.responses.get(creative_key(creative))
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
