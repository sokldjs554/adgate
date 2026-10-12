"""Anthropic(Claude) 구조화 출력 클라이언트. judge 어댑터(``judge_anthropic``)와 같은 요청 원칙을 과제 단위로 일반화했다.

* 구조화 출력(``output_config.format``)으로 스키마에 맞는 JSON 객체만 받는다. 강제 ``tool_choice``·샘플링 인자·``budget_tokens`` 는 쓰지 않는다
  (최신 모델은 400 이다). 깊이는 ``output_config.effort`` 로만 조절한다.
* 시스템 프롬프트는 *고정된* 블록이므로 끝에 캐시 표지를 둔다(소재마다 달라지는 사용자 입력에는 붙이지 않는다).
* 사용자 입력의 이미지는 base64 블록으로 보낸다. 입력 글은 모두 '신뢰할 수 없는 데이터'로 감싸 보내는 것은 과제(프롬프트)의 몫이다.
* 어떤 실패(거절, 잘림, 형식 오류, 예산 초과, 네트워크)든 ``LLMError`` 계통 예외를 던진다. 호출한 쪽이 fail-closed 로 처리한다.
* 비용 상한(``max_usd``)을 넘으면 호출하지 않고 ``LLMBudgetExceeded`` 를 던진다.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .core import (
    Image,
    LLMBudgetExceeded,
    LLMProtocolError,
    LLMRefused,
    LLMResult,
    LLMTask,
    LLMTruncated,
    Part,
    RenderedImage,
    parts_of,
)
from .spend import Spend

DEFAULT_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
IMAGE_CHARS = 1600  # 이미지 한 장을 비용 추정에서 글자 수로 환산한 값(1,000~1,600 토큰 안팎)


def parse_message(message: Any) -> dict[str, Any]:
    """API 응답에서 JSON 객체를 꺼낸다. 어떤 이상이든 ``LLMError`` 로 바꿔 던진다."""
    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        details = getattr(message, "stop_details", None)
        raise LLMRefused(f"category={getattr(details, 'category', None)}")
    if stop == "max_tokens":
        raise LLMTruncated("max_tokens 에서 잘렸습니다")
    text = next((b.text for b in getattr(message, "content", []) if getattr(b, "type", None) == "text"), None)
    if text is None:
        raise LLMProtocolError("텍스트 블록이 없습니다")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMProtocolError("JSON 이 아닙니다") from exc
    if not isinstance(data, dict):
        raise LLMProtocolError("JSON 객체가 아닙니다")
    return data


class AnthropicClient:
    name = "anthropic"

    def __init__(
        self, *, model: str = DEFAULT_MODEL, max_usd: float | None = 5.0, client: Any = None, timeout: float = 60.0,
        max_retries: int = 1, fallbacks: bool = False, base_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.fallbacks = fallbacks
        self.base_dir = base_dir or Path.cwd()
        self.spend = Spend(model, max_usd, exc=LLMBudgetExceeded)
        if client is None:
            import anthropic  # 지연 임포트: 선택 의존성

            client = anthropic.Anthropic(timeout=timeout, max_retries=max_retries)
        self.client = client
        self._tls = threading.local()

    @property
    def last_meta(self) -> dict[str, Any]:
        """이 스레드의 마지막 ``call`` 정보(지연, 토큰, 종료 사유). 호출 전에 실패했으면 비어 있다."""
        return dict(getattr(self._tls, "meta", {}))

    def _block(self, part: Part) -> dict[str, Any]:
        if isinstance(part, RenderedImage):
            # 지연 임포트: 실제 호출 때만 그린다(재생에는 Pillow 도 필요 없다)
            from ..vision.render import render_png_bytes

            data = base64.standard_b64encode(render_png_bytes(part.spec)).decode("ascii")
            return {"type": "image", "source": {"type": "base64", "media_type": part.media_type, "data": data}}
        if isinstance(part, Image):
            data = base64.standard_b64encode((self.base_dir / part.path).read_bytes()).decode("ascii")
            return {"type": "image", "source": {"type": "base64", "media_type": part.media_type, "data": data}}
        return {"type": "text", "text": part}

    def request(self, task: LLMTask, user: str | Sequence[Part]) -> dict[str, Any]:
        """messages.create 에 넘길 인자. 부수 효과가 없어서 테스트가 형태를 직접 검사한다."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": task.max_tokens,
            "system": [{"type": "text", "text": task.system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": [self._block(p) for p in parts_of(user)]}],
            "output_config": {"effort": task.effort, "format": {"type": "json_schema", "schema": task.schema}},
        }
        if self.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def estimate(self, task: LLMTask, user: str | Sequence[Part]) -> float:
        chars = len(task.system) + sum(len(p) if isinstance(p, str) else IMAGE_CHARS for p in parts_of(user))
        return self.spend.estimate(chars, expected_output_tokens=task.expected_output_tokens)

    def call(self, task: LLMTask, user: str | Sequence[Part]) -> LLMResult:
        meta: dict[str, Any] = {"model": self.model, "task": task.name}
        self._tls.meta = meta  # 예산 검사에서 막히면 모델 이름만 남는다
        kwargs = self.request(task, user)
        est = self.estimate(task, user)
        self.spend.reserve(est)
        t0 = time.perf_counter()
        try:
            api = self.client.beta.messages if self.fallbacks else self.client.messages
            message = api.create(**kwargs)
        except Exception:
            meta["latency_ms"] = round((time.perf_counter() - t0) * 1000)
            self.spend.settle(est, None)
            self.spend.failures += 1
            raise
        usage = getattr(message, "usage", None)
        meta.update(
            latency_ms=round((time.perf_counter() - t0) * 1000), stop_reason=getattr(message, "stop_reason", None),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0), output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        )
        self.spend.settle(est, usage)
        try:
            return LLMResult(parse_message(message), meta)
        except LLMRefused:
            self.spend.refusals += 1
            raise
        except Exception:
            self.spend.failures += 1
            raise
