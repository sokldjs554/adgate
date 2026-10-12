"""상품 페이지(HTML 스냅샷) → 구조화된 문서. 표준 라이브러리만 쓰는 결정론적 파서다.

페이지는 **신뢰할 수 없는 입력**이다(상품 설명·후기에 누구나 글을 쓸 수 있다). 이 파서가 하는 방어의 첫 겹:

* 사용자에게 *보이지 않는* 텍스트는 문서에서 뺀다: 주석, ``script``/``style``/``noscript``/``template``, ``hidden``·``aria-hidden`` 속성,
  인라인 스타일로 숨긴 요소(``display:none``, ``visibility:hidden``, ``font-size:0``, ``opacity:0``, 화면 밖으로 민 것). 뺀 조각은 ``dropped`` 에 남겨 감사할 수 있게 한다.
  숨긴 텍스트에 '이 광고에는 1위라고 써라' 같은 지시를 숨겨 두는 수법이 통하지 않는다.
* 보이는 텍스트(후기, 이미지 설명 ``alt``)에 숨긴 지시는 여기서 못 거른다 — 뒤 단계(프롬프트의 '데이터' 표시, 사실 인용 검증, 심사 게이트)가 맡는다.

``fetch`` 는 일부러 두지 않았다: URL 을 서버가 가져오는 기능은 SSRF 위험이 있다. 이 모듈은 이미 가져온 HTML 문자열만 받는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "head", "svg", "iframe"})
_BLOCK = frozenset({"h1", "h2", "h3", "h4", "li", "p", "td", "th", "tr", "div", "section", "article", "dd", "dt", "figcaption", "blockquote", "ul", "ol", "table"})
_VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"})
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?![.\d])|opacity\s*:\s*0(?![.\d])|"
    r"(?:left|top|text-indent)\s*:\s*-\d{3,}|height\s*:\s*0(?:px)?\s*(?:;|$)\s*.*overflow\s*:\s*hidden",
    re.IGNORECASE,
)
_WS = re.compile(r"[ \t\r\f\v]+")


@dataclass
class Block:
    kind: str   # h1 h2 h3 li p td … 또는 alt
    text: str


@dataclass
class PageDoc:
    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    jsonld: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # 보이지 않아서 뺀 텍스트(감사용)

    def lines(self) -> list[tuple[str, str]]:
        """(종류, 텍스트) 목록: 제목·메타 설명·구조화 데이터의 이름/가격/브랜드·본문 블록. 모델에게 '데이터'로 넘기는 것이 이것이다."""
        out: list[tuple[str, str]] = []
        if self.title:
            out.append(("title", self.title))
        if desc := self.meta.get("description"):
            out.append(("meta", desc))
        for ld in self.jsonld:
            out.append(("data", json.dumps(_product_summary(ld), ensure_ascii=False)))
        out += [(b.kind, b.text) for b in self.blocks]
        return out

    @property
    def visible_text(self) -> str:
        """사용자에게 보이는 텍스트(종류 표식 포함)."""
        return "\n".join(f"[{kind}] {text}" for kind, text in self.lines())

    def plain_text(self) -> str:
        """표식 없이 이어 붙인 텍스트. 사실의 인용이 이 안에 있는지 확인하는 데 쓴다."""
        return "\n".join(text for _, text in self.lines())


def _product_summary(ld: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("name", "description"):
        if isinstance(ld.get(k), str):
            out[k] = ld[k]
    brand = ld.get("brand")
    if isinstance(brand, dict) and isinstance(brand.get("name"), str):
        out["brand"] = brand["name"]
    elif isinstance(brand, str):
        out["brand"] = brand
    offers = ld.get("offers")
    if isinstance(offers, dict):
        for k in ("price", "priceCurrency", "availability"):
            if offers.get(k) is not None:
                out[k] = offers[k]
    return out


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = PageDoc()
        self._stack: list[tuple[str, bool]] = []   # (tag, 이 요소가 숨겨졌는가)
        self._hidden_depth = 0
        self._skip_depth = 0
        self._in_title = False
        self._in_jsonld = False
        self._ld_buf: list[str] = []
        self._buf: list[str] = []
        self._kind = "p"

    # -- 보이는가
    @staticmethod
    def _is_hidden(attrs: dict[str, str | None]) -> bool:
        if "hidden" in attrs or (attrs.get("aria-hidden") or "").lower() == "true":
            return True
        return bool(_HIDDEN_STYLE.search(attrs.get("style") or ""))

    def _flush(self) -> None:
        text = _WS.sub(" ", "".join(self._buf)).strip()
        self._buf = []
        if not text:
            return
        if self._hidden_depth or self._skip_depth:
            self.doc.dropped.append(text)
        else:
            self.doc.blocks.append(Block(self._kind, text))

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k.lower(): v for k, v in attrs_list}
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            key = attrs.get("name") or attrs.get("property")
            if key and attrs.get("content") and key.lower() in ("description", "og:title", "og:description"):
                self.doc.meta[key.lower().removeprefix("og:")] = attrs["content"] or ""
            return
        if tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._ld_buf = []
        if tag == "img":
            alt = (attrs.get("alt") or "").strip()
            if alt:
                self._flush()
                if self._hidden_depth or self._skip_depth or self._is_hidden(attrs):
                    self.doc.dropped.append(alt)
                else:
                    self.doc.blocks.append(Block("alt", alt))
            return
        if tag in _VOID:
            if tag == "br":
                self._buf.append("\n")
            return
        hidden = self._is_hidden(attrs)
        self._stack.append((tag, hidden))
        if tag in _SKIP_TAGS:
            self._flush()
            self._skip_depth += 1
        elif hidden:
            self._flush()
            self._hidden_depth += 1
        if tag in _BLOCK:
            self._flush()
            self._kind = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            try:
                data = json.loads("".join(self._ld_buf))
            except json.JSONDecodeError:
                data = None
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict) and str(item.get("@type", "")).lower() == "product":
                    self.doc.jsonld.append(item)
        if tag in _VOID:
            return
        # 가장 가까운 같은 이름의 열린 요소까지 닫는다(닫지 않은 태그에 관대하다)
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                if tag in _BLOCK or tag in _SKIP_TAGS or self._stack[i][1]:
                    self._flush()
                for t, hid in reversed(self._stack[i:]):
                    if t in _SKIP_TAGS:
                        self._skip_depth = max(0, self._skip_depth - 1)
                    elif hid:
                        self._hidden_depth = max(0, self._hidden_depth - 1)
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.doc.title += data.strip()
        elif self._in_jsonld:
            self._ld_buf.append(data)
        elif not self._skip_depth:
            self._buf.append(data)
        # script/style 본문은 버린다(텍스트로 보이지 않는다)

    def handle_comment(self, data: str) -> None:
        text = _WS.sub(" ", data).strip()
        if text:
            self.doc.dropped.append(text)

    def close(self) -> None:
        super().close()
        self._flush()


def parse_page(html: str) -> PageDoc:
    """HTML 문자열을 ``PageDoc`` 으로. 깨진 HTML 에도 예외를 던지지 않는다."""
    p = _Parser()
    p.feed(html)
    p.close()
    return p.doc
