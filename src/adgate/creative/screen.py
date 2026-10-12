"""사실 심사: 추출한 사실 *하나하나*를 광고 문구인 것처럼 같은 정책 파이프라인에 통과시켜, 광고로 옮기면 위반이 될 사실을 생성 전에 거른다.

문서 전체의 규칙(광고주 인증 ADV, 금융 필수 고지 FIN)과 인용이 없는 발견은 문장 하나의 문제가 아니므로 무시한다.
페이지가 스스로 하는 질병 효능·보장·근거 없는 1위 같은 주장이 후보 생성기에 도달하지 못하게 하는 두 번째 방어선이다(첫째는 숨긴 텍스트 제거).
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import ReviewConfig
from ..models import Advertiser, Creative
from ..pipeline import review
from ..policy import Policy
from .models import Fact

DOCUMENT_LEVEL_RULES = frozenset({"AD-ADV-01", "AD-FIN-01"})


def screen_facts(
    facts: Sequence[Fact], advertiser: Advertiser, policy: Policy,
) -> tuple[list[Fact], list[dict[str, str]]]:
    kept: list[Fact] = []
    out: list[dict[str, str]] = []
    for f in facts:
        creative = Creative(ad_id=f"fact-{f.id}", advertiser=advertiser, headline="", body=f.text, cta="")
        result = review(creative, ReviewConfig(), policy)
        hits = sorted({
            x.rule_id for x in result.findings
            if x.severity != "info" and x.quote and x.rule_id not in DOCUMENT_LEVEL_RULES
        })
        if hits:
            out.append({"id": f.id, "text": f.text, "reason": ",".join(hits)})
        else:
            kept.append(f)
    return kept, out
