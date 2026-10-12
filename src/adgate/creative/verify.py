"""후보의 결정론 검증. 심사(정책)와 별개로 *이 광고주·이 상품*에 대한 약속을 지켰는지 본다.

* 사실 인용: ``fact_ids`` 가 있고 모두 쓸 수 있는 사실이다.
* 숫자 충실도: 제목·본문·이미지 문구의 모든 숫자가 인용한 사실(또는 광고주 이름·필수 용어)에 있다. 지어낸 가격·수치를 잡는 가장 싼 방어선이다.
* 형식: 글자 수 상한, 허용 CTA, 이미지 문구가 그려지는가.
* 브랜드 보이스: 필수 용어, 금지어, 말투(합니다체/해요체).
* 숨긴 텍스트 되풀이: 파서가 숨겨진 것으로 뺀 문장을 후보가 베꼈는가(숨긴 지시를 따랐다는 신호).

검증이 통과해도 '사실에 근거한다'가 증명되는 것은 아니다(숫자가 없는 주장은 이 검사로 못 잡는다). 그 잔여는 독립 감사가 잰다(docs/EVAL_CREATIVE.md).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ..models import Advertiser
from ..vision import ImageSpec
from ..vision import problems as render_problems
from .layout import image_spec_for
from .models import Candidate, Constraints, Fact, Problem
from .products import BrandVoice

_DIGITS = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WS = re.compile(r"\s+")
_SENT = re.compile(r"[^.!?\n]+")
_STRIP_END = re.compile(r"[\s.!?~…·ㅎㅋ​😀-🙏✨⭐️❤️👍]+$")


def digit_tokens(text: str) -> set[str]:
    return {m.replace(",", "").rstrip(".") for m in _DIGITS.findall(text)}


def _squash(text: str) -> str:
    return _WS.sub("", text)


def _core(text: str) -> str:
    """글자·숫자만 남긴다(따옴표·문장부호·공백을 바꿔 베낀 문장도 같게 보려고)."""
    return re.sub(r"[^\w]", "", text)


def tone_violations(text: str, tone: str) -> list[str]:
    """말투가 어긋난 문장. 명사형으로 끝나는 문장은 위반이 아니다(광고에 흔하다)."""
    out: list[str] = []
    for m in _SENT.finditer(text):
        s = _STRIP_END.sub("", m.group().strip())
        if not s:
            continue
        if tone == "합니다체" and s.endswith("요"):
            out.append(s[-12:])
        elif tone == "해요체" and re.search(r"(?:습니다|입니다|ㅂ니다|합니다|됩니다|십시오)$", s):
            out.append(s[-12:])
    return out


def verify(
    c: Candidate, facts: Mapping[str, Fact], advertiser: Advertiser, voice: BrandVoice, cons: Constraints, *,
    product_image: ImageSpec | None = None, hidden_texts: Sequence[str] = (),
) -> list[Problem]:
    out: list[Problem] = []

    def bad(code: str, message: str, quote: str | None = None) -> None:
        out.append(Problem(kind="verify", code=code, message=message, quote=quote, fix=None))

    # 사실 인용
    if not c.fact_ids:
        bad("no_fact_ids", "근거로 쓴 사실 id(fact_ids)가 없습니다.")
    unknown = [i for i in c.fact_ids if i not in facts]
    if unknown:
        bad("unknown_fact_id", f"목록에 없는 사실 id: {', '.join(unknown)}", unknown[0])
    cited = [facts[i] for i in c.fact_ids if i in facts]
    # 숫자 충실도 — 제목·본문·이미지 문구는 인용한 사실만, 고지는 모든 사실도 허용(고지가 사실 목록의 금리 같은 것을 되풀이할 수 있다)
    base = " ".join([advertiser.name, *voice.required_terms])
    allowed = digit_tokens(" ".join(f.text for f in cited) + " " + base)
    for tok in sorted(digit_tokens(" ".join([c.headline, c.body, c.image.overlay_text, c.image.badge_text or ""])) - allowed):
        bad("invented_number", f"인용한 사실에 없는 숫자 '{tok}' 가 있습니다. 사실에 적힌 숫자만 그대로 쓰세요.", tok)
    if c.disclosure:
        allowed_all = allowed | digit_tokens(" ".join(f.text for f in facts.values()))
        for tok in sorted(digit_tokens(c.disclosure) - allowed_all):
            bad("invented_number", f"고지에 사실 목록에 없는 숫자 '{tok}' 가 있습니다.", tok)
    # 형식
    max_head = min(cons.max_headline_chars, voice.max_headline_chars)
    if len(c.headline) > max_head:
        bad("headline_too_long", f"제목이 {len(c.headline)}자입니다(≤{max_head}).")
    if len(c.body) > cons.max_body_chars:
        bad("body_too_long", f"본문이 {len(c.body)}자입니다(≤{cons.max_body_chars}).")
    if len(c.image.overlay_text) > cons.max_overlay_chars:
        bad("overlay_too_long", f"이미지 문구가 {len(c.image.overlay_text)}자입니다(≤{cons.max_overlay_chars}).")
    if c.image.badge_text and len(c.image.badge_text) > cons.max_badge_chars:
        bad("badge_too_long", f"배지 문구가 {len(c.image.badge_text)}자입니다(≤{cons.max_badge_chars}).")
    if c.cta not in cons.ctas:
        bad("cta_not_allowed", f"허용 목록에 없는 CTA '{c.cta}'.", c.cta)
    if c.headline.strip() == c.body.strip():
        bad("headline_equals_body", "제목과 본문이 같습니다.")
    # 브랜드 보이스
    visible = _squash(" ".join([c.headline, c.body, c.image.overlay_text]))
    for term in voice.required_terms:
        if _squash(term) not in visible:
            bad("missing_required_term", f"필수 용어 '{term}' 가 제목·본문·이미지 문구에 없습니다.", term)
    everything = _squash(" ".join([c.headline, c.body, c.cta, c.disclosure or "", c.image.overlay_text, c.image.badge_text or ""]))
    for term in voice.banned_terms:
        if _squash(term) in everything:
            bad("banned_term", f"금지어 '{term}' 가 들어 있습니다.", term)
    for frag in tone_violations(c.headline, voice.tone) + tone_violations(c.body, voice.tone):
        bad("tone_mismatch", f"말투가 {voice.tone} 와 어긋납니다: …{frag}", frag)
    # 숨긴 텍스트 되풀이(8자 이상 겹치면)
    squashed = _core(" ".join([c.headline, c.body, c.image.overlay_text, c.image.badge_text or "", c.disclosure or ""]))
    for h in hidden_texts:
        hs = _core(h)
        if len(hs) >= 8 and any(hs[i:i + 8] in squashed for i in range(len(hs) - 7)):
            bad("echoes_hidden_text", "페이지에서 사용자에게 보이지 않게 숨겨진 문장을 베꼈습니다.", h[:30])
            break
    # 이미지가 그려지는가
    if product_image is not None:
        for p in render_problems(image_spec_for(c, product_image, cons)):
            bad("render_failed", p)
    return out
