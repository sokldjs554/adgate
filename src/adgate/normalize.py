"""텍스트 정규화. 우회 표기를 걷어내되, 모든 문자의 원문 위치를 보존한다.

두 가지 보기(view)를 만든다.

* ``soft``: NFKC(전각→반각 등) + 소문자 + 제로폭·서식 문자 제거 + 한자·유사 문자 접기 + 분해형 한글 자모 합치기
  + 글자 사이를 띄운 낱말("치 매 예 방") 붙이기. 일반 띄어쓰기는 그대로.
  문장 경계나 `\\s*` 가 필요한 규칙(질병명 경계, 근거 표시 등)이 쓴다.
* ``tight``: ``soft`` 에서 공백을 모두 없애고, 글자와 글자 사이에 끼운 구분 기호(`.`, `-`, `_`, `*` …)와 장식 기호(`★`, 이모지 …)도 없앤다.
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
SEPARATORS = ".·‧∙ㆍᆞ-‐‑‒–—―_*~^'\"`|/\\•()[]{}「」『』【】〈〉《》〔〕"
# 장식 기호: ※, 화살표, 기술 기호, 도형·기호·딩뱃(★ ♥ ■ ▶ …), 이모지. 단어 사이에 끼우면 구분 기호처럼 쓰인다.
_DECORATIONS = "\u203b\u2190-\u21ff\u2300-\u23ff\u2500-\u27bf\u2b00-\u2bff\U0001f000-\U0001faff"
_SEP_CLASS = re.escape(SEPARATORS) + _DECORATIONS

# 1:1 로 접는 문자(위치가 그대로라서 원문 오프셋이 보존된다).
# * 키릴·그리스 유사 문자 → 라틴: 'саsіnо' 처럼 모양이 같은 다른 문자로 어휘를 피하는 우회
# * 한자 → 한글 독음: 정책 어휘에 쓰이는 글자만('保障', '無條件' …). 모든 한자를 다루지는 않는다.
_CONFUSABLES = {
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y", "і": "i", "ї": "i", "ј": "j", "ѕ": "s",
    "к": "k", "ԁ": "d", "ԛ": "q", "ԝ": "w", "ɡ": "g", "ı": "i",
    "α": "a", "ο": "o", "ρ": "p", "ν": "v", "ι": "i", "κ": "k", "ε": "e", "τ": "t", "υ": "u", "χ": "x",
}
_HANJA = {
    "保": "보", "障": "장", "無": "무", "条": "조", "條": "조", "件": "건", "確": "확", "定": "정", "證": "증", "证": "증",
    "完": "완", "治": "치", "療": "료", "疗": "료", "豫": "예", "予": "예", "預": "예", "防": "방", "最": "최", "高": "고",
    "低": "저", "唯": "유", "一": "일", "位": "위", "收": "수", "益": "익", "元": "원", "金": "금", "承": "승",
    "認": "인", "认": "인", "絕": "절", "絶": "절", "对": "대", "對": "대", "壁": "벽", "璧": "벽", "國": "국", "内": "내",
    "內": "내", "第": "제", "百": "백", "全": "전", "額": "액", "還": "환", "給": "급", "利": "이", "損": "손", "失": "실",
}
_FOLD = str.maketrans({**_CONFUSABLES, **_HANJA})

# 눈에 보이지 않는 채움 문자(한글 채움 문자, 점자 빈칸). 글자 사이에 끼워 단어를 쪼개는 데 쓰인다 → soft 에서 지운다.
_FILLERS = frozenset("\u115f\u1160\u3164\uffa0\u2800")
# 글자 방향을 바꾸는 제어 문자(RLO 등): 논리 순서와 화면에 보이는 순서가 달라져 어휘 검사가 속는다 → 지우는 대신 사람이 본다.
_BIDI = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def suspicious_characters(text: str) -> list[str]:
    """보이지 않는 채움 문자(``filler``)나 글자 방향을 바꾸는 제어 문자(``bidi``)가 있는가. 있으면 파이프라인이 사람에게 넘긴다."""
    found: list[str] = []
    if any(c in _BIDI for c in text):
        found.append("bidi")
    if any(unicodedata.normalize("NFKC", c) in _FILLERS or c in _FILLERS for c in text):
        found.append("filler")
    return found
_WORD = r"[0-9a-z가-힣ᄀ-ᇿ㄰-㆏%]"  # % 는 "100■%", "100%★승인" 처럼 숫자·낱말과 붙어 쓰이므로 낱말 문자로 본다
# 구분 기호가 글자에 바로 붙어 있거나(수.익), 앞뒤에 공백이 낀 경우(수 - 익, N o . 1) 모두 단어를 쪼개는 용도로 본다.
# 마침표 뒤에만 공백이 오는 "수익. 보장"은 문장 경계이므로 건드리지 않는다.
_SEP_RUN = re.compile(rf"(?<={_WORD})(?:[{_SEP_CLASS}]+|\s+[{_SEP_CLASS}]+\s*)(?={_WORD})")
_SENTENCE_END = re.compile(r"[.!?。]+(?=\s|$)|\n")
# 글자 사이를 하나씩 띄운 낱말("치 매 예 방에", "국\n내\n1\n위"): 낱글자(한글·영문·숫자) 3개 이상이 한두 칸 공백·줄바꿈으로 이어진 덩어리.
# 마지막 글자 뒤에 조사 한 글자가 바로 붙어도("방에") 덩어리로 본다. 그 외의 한글이 붙으면("첫 달 천원"의 '천원') 낱말의 일부이므로 붙이지 않는다.
# 길이를 40 으로 묶어 선형 시간을 지킨다(끝이 맞지 않는 긴 덩어리를 시작 위치마다 다시 훑지 않게). 숫자만 이어진 덩어리("1 2 3")는 붙이지 않는다.
_SPACED_LETTERS = re.compile(
    r"(?<![0-9a-z가-힣])[0-9a-z가-힣](?:[ \n]{1,2}[0-9a-z가-힣]){2,40}(?:(?![0-9a-z가-힣])|(?=[은는이가을를에의도과와로만](?![가-힣])))"
)

Mode = Literal["full", "plain"]


def _soft(text: str) -> tuple[str, list[int], list[int]]:
    """(soft 문자열, 각 문자의 원문 시작 위치, 각 문자의 원문 끝 위치[배타]). 1:1 접기는 위치를 그대로 두고,
    분해형 자모 합치기(여러 문자 → 한 음절)와 낱글자 붙이기는 원문 구간을 넓혀 기억한다."""
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for i, c in enumerate(text):
        cat = unicodedata.category(c)
        if cat in ("Cf", "Mn"):  # 제로폭, 소프트 하이픈, 결합 부호 …
            continue
        if cat == "Cc" and c not in "\n\t\r":
            continue
        for ch in unicodedata.normalize("NFKC", c).lower().translate(_FOLD):
            if ch in _FILLERS:
                continue
            chars.append(ch)
            starts.append(i)
            ends.append(i + 1)
    chars, starts, ends = _compose_jamo(chars, starts, ends)
    soft, starts, ends = _join_spaced_letters(chars, starts, ends)
    return _drop_separator_runs(soft, starts, ends)


def _compose_jamo(chars: list[str], starts: list[int], ends: list[int]) -> tuple[list[str], list[int], list[int]]:
    """분해형(NFD) 한글 자모열 초성+중성[+종성] 을 한 음절로 합친다. 문자 단위 NFKC 로는 합쳐지지 않기 때문이다."""
    out_c: list[str] = []
    out_s: list[int] = []
    out_e: list[int] = []
    i, n = 0, len(chars)
    while i < n:
        c = ord(chars[i])
        if 0x1100 <= c <= 0x1112 and i + 1 < n and 0x1161 <= ord(chars[i + 1]) <= 0x1175:
            j = i + 2
            tail = 0
            if j < n and 0x11A8 <= ord(chars[j]) <= 0x11C2:
                tail = ord(chars[j]) - 0x11A7
                j += 1
            out_c.append(chr(0xAC00 + ((c - 0x1100) * 21 + (ord(chars[i + 1]) - 0x1161)) * 28 + tail))
            out_s.append(starts[i])
            out_e.append(ends[j - 1])
            i = j
        else:
            out_c.append(chars[i])
            out_s.append(starts[i])
            out_e.append(ends[i])
            i += 1
    return out_c, out_s, out_e


_WORD_CH = re.compile(_WORD)


def _is_word(soft: str, i: int) -> bool:
    return 0 <= i < len(soft) and _WORD_CH.match(soft[i]) is not None


def _separator_runs(soft: str) -> list[tuple[int, int, bool]]:
    """(시작, 끝, 낱자 사이의 공백 낀 구분인가) 목록. 숫자 사이의 기호(3.5, 2026-10, 9~18)는 뺀다."""
    out: list[tuple[int, int, bool]] = []
    for m in _SEP_RUN.finditer(soft):
        a, b = m.span()
        if soft[a - 1].isdigit() and soft[b].isdigit():
            continue
        spaced = any(c.isspace() for c in soft[a:b])
        singles = not _is_word(soft, a - 2) and not _is_word(soft, b + 1)  # 양쪽 낱말이 한 글자
        out.append((a, b, not spaced or singles))
    return out


def _drop_separator_runs(soft: str, starts: list[int], ends: list[int]) -> tuple[str, list[int], list[int]]:
    """낱말 사이에 끼운 구분·장식 기호("수.익.보.장", "무★조★건", "수(익)보(장)")를 soft 에서도 걷어 낸다.
    soft 에서 찾는 규칙(질병명 경계 등)도 같은 우회에 속지 않게 하려는 것이다. 공백이 낀 구분 기호는 양쪽이 한 글자짜리 낱말일 때만 걷는다
    ("당 · 뇨" 는 걷고, "건강 · 당뇨 예방" 의 낱말 경계는 지키지 않으면 질병명이 앞 낱말에 붙어 버린다)."""
    drop: set[int] = set()
    for a, b, safe in _separator_runs(soft):
        if safe:
            drop.update(range(a, b))
    if not drop:
        return soft, starts, ends
    keep = [i for i in range(len(soft)) if i not in drop]
    return "".join(soft[i] for i in keep), [starts[i] for i in keep], [ends[i] for i in keep]


def _join_spaced_letters(chars: list[str], starts: list[int], ends: list[int]) -> tuple[str, list[int], list[int]]:
    """"치 매 예 방" 처럼 낱글자를 띄워 쓴 덩어리의 공백을 걷어 낸다(문자 경계가 필요한 soft 규칙이 우회당하지 않게)."""
    soft = "".join(chars)
    drop: set[int] = set()
    for m in _SPACED_LETTERS.finditer(soft):
        if "".join(m.group().split()).isdigit():
            continue
        drop.update(i for i in range(m.start(), m.end()) if soft[i] in " \n")
    if not drop:
        return soft, starts, ends
    keep = [i for i in range(len(soft)) if i not in drop]
    return "".join(soft[i] for i in keep), [starts[i] for i in keep], [ends[i] for i in keep]


@dataclass(frozen=True)
class Normalized:
    original: str
    soft: str
    soft_to_orig: tuple[int, ...]
    tight: str
    tight_to_soft: tuple[int, ...]
    soft_end: tuple[int, ...] = ()  # soft 문자가 덮는 원문의 끝(배타). 비어 있으면 시작+1(1:1 대응)

    def _end(self, soft_index: int) -> int:
        return self.soft_end[soft_index] if self.soft_end else self.soft_to_orig[soft_index] + 1

    def orig_span_from_tight(self, a: int, b: int) -> tuple[int, int]:
        """tight[a:b] 에 해당하는 원문 [start, end)."""
        return self.soft_to_orig[self.tight_to_soft[a]], self._end(self.tight_to_soft[b - 1])

    def orig_span_from_soft(self, a: int, b: int) -> tuple[int, int]:
        return self.soft_to_orig[a], self._end(b - 1)

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
    soft_end: list[int] | None = None
    if mode == "plain":
        soft = text.lower()
        soft_idx = list(range(len(soft)))
        keep = [i for i, c in enumerate(soft) if c != " "]
    else:
        soft, soft_idx, soft_end = _soft(text)
        # tight 는 soft 에서 공백과, 아직 남은 모든 구분 기호(공백이 낀 것 포함)를 더 걷어 낸다.
        drop = {i for a, b, _ in _separator_runs(soft) for i in range(a, b)}
        keep = [i for i, c in enumerate(soft) if i not in drop and (c == "\n" or not c.isspace())]
    tight = "".join(soft[i] for i in keep)
    return Normalized(
        original=text,
        soft=soft,
        soft_to_orig=tuple(soft_idx),
        tight=tight,
        tight_to_soft=tuple(keep),
        soft_end=tuple(soft_end) if soft_end is not None else (),
    )
