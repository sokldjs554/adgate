"""모델에게 *데이터*를 보낼 때의 위생. 광고 문구·상품 페이지·이미지 속 글자는 모두 신뢰할 수 없는 입력이다."""

from __future__ import annotations

import json
import re
from typing import Any

# 사람 눈에 안 보이는 서식·채움·태그 문자(예: U+E0000 대역으로 쓴 숨은 지시)는 \uXXXX 로 풀어 모델에게도 *보이게* 한다(독립 리뷰 L5).
_INVISIBLE = re.compile(r"[­͏؜ᅟᅠ឴឵᠋-᠏​-‏‪-‮⁠-⁯⠀ㅤ︀-️﻿ﾠ\U000e0000-\U000e007f]")


def safe_json(value: Any) -> str:
    """JSON 으로 직렬화하되 `<` `>` 를 이스케이프해서, 데이터 속 '</page>' 가 구분자를 흉내 내지 못하게 한다."""
    out = json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    return _INVISIBLE.sub(lambda m: json.dumps(m.group())[1:-1], out)
