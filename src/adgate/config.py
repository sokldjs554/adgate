"""심사 설정. 어블레이션(구성요소를 끄고 지표 변화를 재는 실험)은 이 스위치로 한다."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .judge import Judge


@dataclass(frozen=True)
class ReviewConfig:
    normalize: bool = True    # NFKC·제로폭·구분 기호 제거. 끄면 소문자 + 일반 공백 제거만
    exceptions: bool = True   # 부정문·범위 표현·면책 고지·근거 표시 같은 '위반이 아닌 예'
    challenge: bool = True    # 느슨한 두 번째 검사(변형 표기·필드 걸친 표현)
    landing: bool = True      # 광고-랜딩 수치 대조, 랜딩 근거로 긴급성 확인
    competitors: tuple[str, ...] = ()  # 비교 광고 탐지에 쓸 경쟁 사업자 이름
    judge: Judge | None = None         # 의미 기반 검사(LLM). 없으면 결정론 경로만
    judge_cap_review: bool = False     # True 면 Judge 가 더한 발견은 reject 등급이어도 review 로 낮춘다(LLM 이 혼자 반려하지 못하게)
    escalate_ungrounded: bool = True   # Judge 가 원문에서 확인되지 않는 인용을 보고하면 버리고 끝내지 않고 사람에게 넘긴다

    def describe(self) -> dict[str, object]:
        return {
            "normalize": self.normalize,
            "exceptions": self.exceptions,
            "challenge": self.challenge,
            "landing": self.landing,
            "judge": type(self.judge).__name__ if self.judge is not None else None,
            "judge_cap_review": self.judge_cap_review,
            "escalate_ungrounded": self.escalate_ungrounded,
        }

    def without(self, component: str) -> ReviewConfig:
        """어블레이션용: 구성요소 하나를 끈 사본."""
        if component == "normalize":
            return replace(self, normalize=False)
        if component == "exceptions":
            return replace(self, exceptions=False)
        if component == "challenge":
            return replace(self, challenge=False)
        if component == "landing":
            return replace(self, landing=False)
        raise ValueError(f"unknown component {component}")
