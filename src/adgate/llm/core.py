"""공통 LLM 계층의 자료형. 구조화 출력 호출을 '과제(task)' 단위로 정의하고, 호출마다 *열쇠*를 만들어 기록·재생을 가능하게 한다.

judge 전용이던 녹음·재생(``judge_recorded``)을 일반화한 것이다. judge 경로는 그대로 두었다(이미 한 번 잰 측정을 다시 흔들지 않는다).

* 열쇠는 (과제 이름, 프롬프트 버전, 시스템 프롬프트, 출력 스키마, 사용자 입력 조각들) 의 해시다. 이미지는 *파일 내용*의 해시를 쓴다.
  모델·effort 는 열쇠에 넣지 않는다 — 녹음 파일 하나는 한 번의 측정이고, 어떤 모델로 녹음했는지는 호출 기록에 남긴다.
* 프롬프트·스키마·입력이 바뀌면 열쇠가 바뀌어 낡은 녹음이 조용히 재생되지 않는다(없는 열쇠는 오류다).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .spend import BudgetExceededError


class LLMError(Exception):
    """호출이 쓸 수 있는 결과를 내지 못했다. 호출한 쪽이 fail-closed 로 처리한다."""


class LLMRefused(LLMError):
    """모델이 안전 분류기 때문에 거절했다(stop_reason=refusal)."""


class LLMTruncated(LLMError):
    """max_tokens 에서 잘렸다."""


class LLMProtocolError(LLMError):
    """응답이 약속한 형식이 아니다(텍스트 블록 없음, JSON 아님, 객체 아님)."""


class LLMMissing(LLMError):
    """녹음에 이 호출이 없다(프롬프트가 바뀌었거나 녹음이 덜 됐다)."""


class LLMRecordedFailure(LLMError):
    """녹음된 호출이 실패였다. 재생하면 같은 자리에서 같이 실패한다."""


class LLMBudgetExceeded(LLMError, BudgetExceededError):
    """설정한 비용 상한에 도달했다."""


class LLMAccountError(LLMBudgetExceeded):
    """계정 때문에 더 부를 수 없다(크레딧 소진·인증·권한). 모델이 낸 실패가 아니다 — 측정에 넣지 않고 실행을 멈춘다. 고친 뒤 이어서 돌린다."""


class RunStop(BaseException):
    """측정 하니스가 *실행 전체를 멈추라고* 올리는 신호(비용 상한 · 계정 오류). ``Exception`` 이 아니므로 fail-closed 로 ``LLMError`` 를 삼키는 에이전트 · 파이프라인의
    안전 처리를 지나친다. 운영에서는 맞는 그 처리(‘못 알아들었으니 다시 말씀해 주세요’)가 측정에서는 크레딧이 없는데도 사례가 끝난 것처럼 보이게 했다(점검이 ‘성공’했다, F-33).
    ``HybridClient`` 가 올리고 ``record_cases`` 만 잡는다."""

    def __init__(self, cause: LLMBudgetExceeded) -> None:
        super().__init__(str(cause))
        self.cause = cause


def is_account_error(exc: BaseException) -> bool:
    """SDK 예외가 계정 문제인가: 크레딧 소진(400 'credit balance is too low' 또는 402), 인증 실패(401), 권한 없음(403)."""
    status = getattr(exc, "status_code", None)
    return status in (401, 402, 403) or "credit balance" in str(exc).lower()


@dataclass(frozen=True)
class Image:
    """사용자 입력에 끼우는 이미지. ``path`` 는 읽는 쪽의 기준 폴더에 대한 상대 경로다."""

    path: str
    media_type: str = "image/png"


@dataclass(frozen=True)
class RenderedImage:
    """사양(JSON)으로 그려질 이미지. 열쇠가 *파일 바이트*가 아니라 사양과 렌더러 버전으로 정해진다.

    그린 PNG 의 바이트는 Pillow/FreeType 버전마다 조금씩 달라서, 바이트로 열쇠를 만들면 다른 환경에서 녹음을 재생하지 못한다.
    사양이 같으면 같은 이미지로 본다(렌더러가 바뀌면 ``renderer`` 를 올려 낡은 녹음을 막는다). 실제 호출 때만 그린다.
    """

    spec: str                      # ImageSpec 의 JSON 문자열(정규화돼 있어야 열쇠가 안정적이다)
    renderer: str = "vision-v2"
    media_type: str = "image/png"


Part = str | Image | RenderedImage


@dataclass(frozen=True)
class LLMTask:
    """한 종류의 호출. 이름·버전·시스템 프롬프트·출력 스키마가 같으면 같은 과제다."""

    name: str
    version: str
    system: str
    schema: dict[str, Any]
    max_tokens: int = 4000
    effort: str = "medium"
    expected_output_tokens: int = 800  # 비용 추정용(생각 토큰 포함하지 않는다)


@dataclass
class LLMResult:
    response: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    name: str

    def call(self, task: LLMTask, user: str | Sequence[Part]) -> LLMResult: ...


def parts_of(user: str | Sequence[Part]) -> list[Part]:
    return [user] if isinstance(user, str) else list(user)


_DIGESTS: dict[tuple[str, int, int], str] = {}


def image_digest(path: Path) -> str:
    """이미지 파일 내용의 sha256(앞 16자). 같은 파일은 다시 읽지 않는다."""
    st = path.stat()
    k = (str(path), st.st_mtime_ns, st.st_size)
    if k not in _DIGESTS:
        _DIGESTS[k] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return _DIGESTS[k]


def task_key(task: LLMTask, user: str | Sequence[Part], base_dir: Path | None = None) -> str:
    base = base_dir or Path.cwd()
    shown: list[dict[str, str]] = []
    for p in parts_of(user):
        if isinstance(p, Image):
            shown.append({"img": image_digest(base / p.path), "type": p.media_type})
        elif isinstance(p, RenderedImage):
            shown.append({"rendered": hashlib.sha256(p.spec.encode("utf-8")).hexdigest()[:16], "renderer": p.renderer, "type": p.media_type})
        else:
            shown.append({"t": p})
    payload = {"task": task.name, "version": task.version, "system": task.system, "schema": task.schema, "user": shown}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:20]


# 기술적 실패(일시적이라 다시 불러도 되는 것). 거절·잘림·형식 오류는 모델의 응답이므로 다시 부르지 않는다.
TECHNICAL_ERRORS = frozenset({"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError", "OverloadedError"})
