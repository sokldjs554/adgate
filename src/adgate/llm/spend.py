"""호출 수·토큰·추정 비용 누적(스레드 안전)과 예산 상한. judge 어댑터와 공통 LLM 계층이 함께 쓴다.

비용은 *추정*이다: 호출 전에 입력 글자 수로 상한을 검사해 예산을 잠그고(``reserve``), 응답의 ``usage`` 로 정산한다(``settle``).
"""

from __future__ import annotations

import threading
from typing import Any

# 1M 토큰당 달러(입력, 출력). 비용 '추정'에만 쓴다. 모르는 모델은 가장 비싼 값으로 본다.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}


class BudgetExceededError(Exception):
    """설정한 비용 상한에 도달했다."""


class Spend:
    """호출 수·토큰·추정 비용 누적. ``exc`` 는 상한을 넘을 때 던질 예외 형식이다(judge 는 자기 계통의 예외를 쓴다)."""

    def __init__(self, model: str, max_usd: float | None, *, exc: type[Exception] = BudgetExceededError) -> None:
        self.model, self.max_usd = model, max_usd
        self._exc = exc
        self.calls = self.input_tokens = self.output_tokens = 0
        self.cache_write_tokens = self.cache_read_tokens = 0
        self.refusals = self.failures = 0
        self.reserved = 0.0     # 진행 중인 호출이 쓸 것으로 미리 잡아 둔 추정 비용
        self.failed_usd = 0.0   # 응답(사용량)을 못 받은 호출은 청구됐다고 보고 추정 비용을 그대로 더한다
        self._lock = threading.Lock()

    @property
    def usd(self) -> float:
        p_in, p_out = PRICES.get(self.model, (10.0, 50.0))
        billed_in = self.input_tokens + 1.25 * self.cache_write_tokens + 0.1 * self.cache_read_tokens
        return (billed_in * p_in + self.output_tokens * p_out) / 1_000_000 + self.failed_usd

    def estimate(self, prompt_chars: int, *, expected_output_tokens: int = 800) -> float:
        """호출 전 추정 비용. 한국어는 글자당 토큰 하나로 넉넉히 보고, 출력은 후보 몇 건의 JSON 정도로 본다."""
        p_in, p_out = PRICES.get(self.model, (10.0, 50.0))
        return (prompt_chars * p_in + expected_output_tokens * p_out) / 1_000_000

    def reserve(self, est_usd: float) -> None:
        """호출 전에 추정 비용을 잠그고 예산을 검사한다. 동시에 많은 호출이 들어와도 상한을 넘겨 시작하지 않는다(독립 리뷰 M9: 검사와 기록이 갈려 상한의 45배를 썼다)."""
        with self._lock:
            if self.max_usd is not None and self.usd + self.reserved + est_usd > self.max_usd:
                raise self._exc(
                    f"추정 비용 ${self.usd:.4f} + 진행 중 ${self.reserved:.4f} 에 이 호출 ${est_usd:.4f} 를 더하면 상한 ${self.max_usd:.2f} 를 넘습니다"
                )
            self.reserved += est_usd

    def settle(self, est_usd: float, usage: Any | None) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - est_usd)
            if usage is None:
                self.failed_usd += est_usd
                return
            self.calls += 1
            self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            self.cache_write_tokens += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            self.cache_read_tokens += int(getattr(usage, "cache_read_input_tokens", 0) or 0)

    def summary(self) -> dict[str, Any]:
        return {
            "model": self.model, "calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens, "cache_read_tokens": self.cache_read_tokens,
            "refusals": self.refusals, "failures": self.failures, "estimated_usd": round(self.usd, 6), "max_usd": self.max_usd,
            "failed_usd": round(self.failed_usd, 6),
        }
