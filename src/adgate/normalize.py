"""텍스트 정규화. 우회 표기를 걷어내되, 모든 문자의 원문 위치를 보존한다.

두 가지 보기(view)를 만든다.

* ``soft``: NFKC(전각→반각 등) + 소문자 + 제로폭·서식 문자 제거. 띄어쓰기는 그대로.
  문장 경계나 `\\s*` 가 필요한 규칙(질병명 경계, 근거 표시 등)이 쓴다.
* ``tight``: ``soft`` 에서 공백을 모두 없애고, 글자와 글자 사이에 끼운 구분 기호(`.`, `-`, `_`, `*` …)도 없앤다.
  "수 익 보 장", "수.익.보.장", "수\\u200b익보장" 이 모두 "수익보장" 이 된다. 어휘 규칙은 대부분 이 보기에서 찾는다.

찾은 위치(tight 인덱스)는 ``orig_span`` 으로 원문 [start, end) 로 되돌려서, 인용이 원문 부분 문자열과 정확히 같게 한다.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from functools import cached_property
from typing import Literal

# 글자 사이에 끼워 넣어 단어를 쪼개는 데 쓰이는 구분 기호
SEPARATORS = ".·‧∙ㆍ-‐‑‒–—―_*~^'\"`|/\\•"
_SEP_CLASS = re.escape(SEPARATORS)
_WORD = r"[0-9a-z가-힣ᄀ-ᇿ㄰-㆏]"
# 구분 기호가 글자에 바로 붙어 있거나(수.익), 앞뒤에 공백이 낀 경우(수 - 익, N o . 1) 모두 단어를 쪼개는 용도로 본다.
# 마침표 뒤에만 공백이 오는 "수익. 보장"은 문장 경계이므로 건드리지 않는다.
_SEP_RUN = re.compile(rf"(?<={_WORD})(?:[{_SEP_CLASS}]+|\s+[{_SEP_CLASS}]+\s*)(?={_WORD})")
_SENTENCE_END = re.compile(r"[.!?。]+(?=\s|$)|\n")

Mode = Literal["full", "plain"]


def _soft(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    idx: list[int] = []
    for i, c in enumerate(text):
        cat = unicodedata.category(c)
        if cat in ("Cf", "Mn"):  # 제로폭, 소프트 하이픈, 결합 부호 …
            continue
        if cat == "Cc" and c not in "\n\t\r":
            continue
        for ch in unicodedata.normalize("NFKC", c).lower():
            chars.append(ch)
            idx.append(i)
    return "".join(chars), idx


@dataclass(frozen=True)
class Normalized:
    original: str
    soft: str
    soft_to_orig: tuple[int, ...]
    tight: str
    tight_to_soft: tuple[int, ...]

    def orig_span_from_tight(self, a: int, b: int) -> tuple[int, int]:
        """tight[a:b] 에 해당하는 원문 [start, end)."""
        first = self.soft_to_orig[self.tight_to_soft[a]]
        last = self.soft_to_orig[self.tight_to_soft[b - 1]]
        return first, last + 1

    def orig_span_from_soft(self, a: int, b: int) -> tuple[int, int]:
        return self.soft_to_orig[a], self.soft_to_orig[b - 1] + 1

    def soft_span_from_tight(self, a: int, b: int) -> tuple[int, int]:
        return self.tight_to_soft[a], self.tight_to_soft[b - 1] + 1

    @cached_property
    def _sentence_ends(self) -> list[int]:
        return [m.end() for m in _SENTENCE_END.finditer(self.soft)]

    def sentence_range(self, soft_pos: int) -> tuple[int, int]:
        """soft 인덱스가 속한 문장의 [lo, hi). 문장 끝 부호 뒤에 공백이 오거나 줄바꿈이면 경계."""
        ends = self._sentence_ends
        k = bisect_right(ends, soft_pos)
        lo = ends[k - 1] if k > 0 else 0
        hi = ends[k] if k < len(ends) else len(self.soft)
        return lo, hi


def build(text: str, mode: Mode = "full") -> Normalized:
    """원문을 정규화한다. ``plain`` 은 어블레이션용: 소문자 + 일반 공백(U+0020)만 제거."""
    if mode == "plain":
        soft = text.lower()
        soft_idx = list(range(len(soft)))
        keep = [i for i, c in enumerate(soft) if c != " "]
    else:
        soft, soft_idx = _soft(text)
        drop: set[int] = set()
        for m in _SEP_RUN.finditer(soft):
            a, b = m.span()
            if soft[a - 1].isdigit() and soft[b].isdigit():
                continue  # 3.5 · 2026-10 · 9~18 같은 숫자 표기는 그대로 둔다
            drop.update(range(a, b))
        keep = [
            i for i, c in enumerate(soft)
            if i not in drop and (c == "\n" or not c.isspace())
        ]
    tight = "".join(soft[i] for i in keep)
    return Normalized(
        original=text,
        soft=soft,
        soft_to_orig=tuple(soft_idx),
        tight=tight,
        tight_to_soft=tuple(keep),
    )
