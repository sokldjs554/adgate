"""대화형 캠페인 셋업 어시스턴트. **모델은 말을 값으로 바꾸고(추출) 말을 다듬을 뿐, 무엇이 값이 되는지·무엇을 물을지·언제 끝났는지는 코드가 정한다.**

한 턴의 흐름:

1. ``campaign.extract`` (LLM) — 광고주 발화에서 슬롯 후보를 뽑는다. 값마다 발화에서 글자 그대로 복사한 인용을 붙인다.
2. **근거 검사(코드)** — 인용이 발화에 있는가, 값이 슬롯 형식에 맞는가, 금액·나이는 인용에 숫자가 있는가, URL·앱 ID 는 발화에 글자 그대로 있는가,
   인용이 '이전 지시를 무시하고…' 같은 주입 문장 안이 아닌가. 통과한 것만 상태에 들어간다(출처 ``user``/``derived``). 모델은 ``assumed`` 를 만들지 못한다.
3. **검증기(코드, ``rules.validate``)** 가 오류·경고를 낸다. 광고주의 말도 모델의 판단도 검증기를 건너뛰지 못한다.
4. **대화 정책(코드)** — 상태가 refused / invalid / needs_input / 요약 확인 중 / ready 중 무엇인지, 무엇을 물을지(우선순위순 최대 둘)를 정한다.
   ready 는 *요약을 보여 준 뒤 광고주가 확인했을 때만* 된다. 확인 뒤 값이 하나라도 바뀌면 다시 요약한다.
5. ``campaign.reply`` (LLM, 선택) — 계획을 자연스러운 말로 옮긴다. 계획에 없는 숫자·약속이 있거나 물어야 할 것을 빠뜨리면 버리고 템플릿 답변을 쓴다.

가정할 수 있는 슬롯(노출 위치·성별·지역·나이·입찰·시작일)은 비어 있으면 코드가 기본값을 넣고 요약에서 '가정'으로 표시해 확인을 받는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from ..creative.pipeline import looks_like_injection
from ..llm import LLMClient, LLMError
from .rules import ADSET_MIN_DAILY, HIGH_BUDGET, MIN_DAILY, Issue, missing, plan_adsets, validate
from .schema import ASSUMABLE_DEFAULTS, SLOTS, SlotError, normalize
from .tasks import extract_input, extract_task, reply_input, reply_task

Source = Literal["user", "derived", "assumed"]
Status = Literal["ready", "needs_input", "invalid", "refused", "awaiting_confirm"]
WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")
MAX_QUESTIONS = 2

LABEL = {
    "objective": "목표", "ad_category": "업종", "daily_budget_krw": "일 예산", "total_budget_krw": "총 예산", "start_date": "시작일", "end_date": "종료일",
    "audience.age_min": "최소 연령", "audience.age_max": "최대 연령", "audience.genders": "성별", "audience.regions": "지역", "audience.interests": "관심사",
    "bid_strategy": "입찰 방식", "target_cpa_krw": "목표 CPA", "max_cpc_krw": "최대 CPC", "placements": "노출 위치", "conversion_event": "전환 이벤트",
    "landing_url": "랜딩 URL", "app_id": "앱 ID",
}
OBJECTIVE_KO = {"awareness": "노출(인지도)", "traffic": "방문(트래픽)", "conversion": "전환(구매·가입)", "app_install": "앱 설치", "lead": "상담·문의(리드)"}
CATEGORY_KO = {
    "loan": "대출", "invest": "투자", "supplement": "건강기능식품", "cosmetic": "화장품", "ecommerce": "쇼핑몰·상품 판매", "app": "앱·소프트웨어", "education": "교육",
    "general": "일반 서비스", "adult": "성인", "gambling": "도박",
}
EVENT_KO = {"purchase": "구매", "signup": "회원가입", "add_to_cart": "장바구니", "install": "앱 설치", "inquiry": "상담·문의"}
BID_KO = {"lowest_cost": "최저 비용", "target_cpa": "목표 CPA", "manual_cpc": "수동 CPC"}
GENDER_KO = {"female": "여성", "male": "남성"}
PLACE_KO = {"feed": "피드", "banner": "배너", "story": "스토리", "search": "검색"}

# 광고주 발화에 섞인 '시스템 지시처럼 보이는' 문장(휴리스틱). 모델이 놓쳐도 코드가 그 문장의 값을 받지 않는다.
_CAMPAIGN_INJECTION = re.compile(
    r"(?:검증|제약|제한|규칙|확인|심사)\w{0,3}\s*(?:을|를|은|는)?\s*(?:건너|무시|생략|끄|해제|우회|패스)"
    r"|관리자|시스템\s*(?:모드|프롬프트|지시|메시지|명령)|개발자\s*모드|프롬프트|이전\s*(?:지시|명령|대화)|(?:모든|위의?)\s*(?:규칙|지시)"
    r"|ignore|disregard|jailbreak|override|\bDAN\b",
    re.IGNORECASE,
)
_SENT = re.compile(r"[^.!?\n]+[.!?]?")
_HAS_NUMBER = re.compile(r"[0-9]|[만천백억]")
_HAS_AGE = re.compile(r"[0-9]|대|세")
FORBIDDEN_REPLY = re.compile(r"보장합니다|무조건\s*(?:통과|승인)|100%\s*승인|반드시\s*통과")


# --------------------------------------------------------------------------- 상태
@dataclass
class SlotValue:
    value: Any
    source: Source
    quote: str | None = None
    turn: int = 0


@dataclass
class Session:
    today: date
    verified: bool = True
    slots: dict[str, SlotValue] = field(default_factory=dict)
    turn: int = 0
    last_assistant: str = ""
    summary_shown: bool = False
    confirmed: bool = False
    confirmed_high_budget: bool = False
    asked: dict[str, int] = field(default_factory=dict)

    def known(self) -> dict[str, Any]:
        """광고주가 말했거나 말에서 계산한 값(가정은 제외) — 추출기에게 보여 줄 '이미 아는 것'."""
        return {p: s.value for p, s in self.slots.items() if s.source != "assumed"}

    def user_spec(self) -> dict[str, Any]:
        return self.known()

    def effective_spec(self) -> dict[str, Any]:
        """검증에 쓰는 값: 광고주 값 + 비어 있는 가정 가능 슬롯의 기본값."""
        spec = self.known()
        for path, default in ASSUMABLE_DEFAULTS.items():
            spec.setdefault(path, list(default) if isinstance(default, list) else default)
        spec.setdefault("start_date", (self.today + timedelta(days=1)).isoformat())
        return spec

    def assumed_paths(self) -> list[str]:
        spec = self.effective_spec()
        return [p for p in spec if p not in self.slots]


@dataclass
class Rejected:
    path: str
    quote: str
    reason: str


@dataclass
class TurnResult:
    turn: int
    utterance: str
    intent: str
    status: Status
    message: str
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    proposed: int = 0                       # 모델이 제안한 슬롯 수(근거 검사 전)
    used_fallback: bool = False             # 답변 검증에 실패해 템플릿을 썼다
    llm_error: str | None = None
    state: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- 근거 검사
def injection_spans(utterance: str) -> list[tuple[int, int]]:
    """주입처럼 보이는 문장의 글자 구간."""
    return [m.span() for m in _SENT.finditer(utterance) if looks_like_injection(m.group()) or _CAMPAIGN_INJECTION.search(m.group())]


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _find(utterance: str, quote: str) -> int:
    pos = utterance.find(quote)
    if pos >= 0:
        return pos
    # 공백 차이만 있는 인용은 받는다(위치는 근사)
    sq, su = _squash(quote), _squash(utterance)
    if sq and sq in su:
        return max(0, utterance.find(quote.strip()[:3])) if quote.strip()[:3] in utterance else 0
    return -1


def parse_value(raw: str, path: str) -> Any:
    try:
        v = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        v = raw.strip().strip('"')
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if SLOTS[path] == "int" and isinstance(v, str) and re.fullmatch(r"\d+", v.strip()):
        v = int(v)
    return v


def ground(items: list[dict[str, Any]], utterance: str) -> tuple[list[dict[str, Any]], list[Rejected]]:
    """모델이 제안한 슬롯을 코드가 다시 검사한다. 통과한 것만 ``accepted``."""
    spans = injection_spans(utterance)
    ok: list[dict[str, Any]] = []
    bad: list[Rejected] = []
    for it in items:
        path, quote = str(it.get("path", "")), str(it.get("quote", ""))
        if path not in SLOTS:
            bad.append(Rejected(path, quote, "unknown_path"))
            continue
        q = quote.strip()
        pos = _find(utterance, q) if q else -1
        if pos < 0:
            bad.append(Rejected(path, quote, "quote_not_in_utterance"))
            continue
        if any(a <= pos < b or a < pos + len(q) <= b for a, b in spans):
            bad.append(Rejected(path, quote, "injection_sentence"))
            continue
        try:
            value = normalize(path, parse_value(str(it.get("value_json", "")), path))
        except SlotError:
            bad.append(Rejected(path, quote, "bad_value"))
            continue
        kind = SLOTS[path]
        if kind == "int" and path.startswith("audience.age") and not _HAS_AGE.search(q):
            bad.append(Rejected(path, quote, "no_number_in_quote"))
            continue
        if kind == "int" and not path.startswith("audience.age") and not _HAS_NUMBER.search(q):
            bad.append(Rejected(path, quote, "no_number_in_quote"))
            continue
        if kind in ("url", "str"):
            core = str(value).removeprefix("https://").rstrip("/").lower()
            if core not in utterance.lower():
                bad.append(Rejected(path, quote, "not_verbatim"))
                continue
        ok.append({"path": path, "value": value, "quote": q, "derived": bool(it.get("derived", False))})
    return ok, bad


# --------------------------------------------------------------------------- 템플릿 문장
QUESTION = {
    "objective": ("이번 캠페인의 목표는 무엇인가요? 노출(알리기)·방문·구매/가입 같은 전환·앱 설치·상담 신청 중에서 알려 주세요.", "목표"),
    "ad_category": ("어떤 상품이나 서비스를 광고하시나요? (예: 쇼핑몰, 화장품, 학원, 앱, 대출·투자 등)", "상품|서비스|업종"),
    "daily_budget_krw": ("하루에 쓰실 예산은 얼마로 생각하세요?", "예산"),
    "landing_url": ("광고를 눌렀을 때 연결할 페이지 주소(https://…)를 알려 주세요.", "주소|URL|페이지"),
    "app_id": ("광고할 앱의 앱 ID(패키지 이름)를 알려 주세요.", "앱 ID|앱 아이디|패키지"),
    "conversion_event": ("어떤 행동을 전환으로 볼까요? 구매·회원가입·장바구니·앱 설치·상담 문의 중에서 골라 주세요.", "전환"),
    "target_cpa_krw": ("목표 CPA(전환 1건당 쓰려는 비용)를 알려 주세요. 최소 500원입니다.", "CPA"),
    "max_cpc_krw": ("최대 CPC(클릭 1회당 최대 비용)를 알려 주세요. 최소 50원입니다.", "CPC"),
}
ISSUE_HELP = {
    "C01_min_daily_budget": "일 예산을 최소 금액 이상으로 올리거나, 최소 금액이 더 낮은 목표로 바꿀 수 있어요.",
    "C02_budget_inconsistent": "총 예산을 늘리거나 일 예산·기간을 줄여야 해요.",
    "C03_bid_strategy": "입찰 방식을 목표에 맞게 바꾸거나 값을 최소 이상으로 올려 주세요.",
    "C04_adset_budget": "일 예산을 늘려야 광고 세트를 나눌 수 있어요.",
    "C05_dates": "시작일은 오늘 이후, 종료일은 시작일 이후로 정해 주세요.",
    "C06_age": "연령은 14–80세이고 최소가 최대보다 클 수 없어요.",
    "C06_age_restricted": "이 업종은 만 19세 이상만 대상으로 할 수 있어요. 최소 연령을 19세 이상으로 바꿔 주세요.",
    "C07_regions": "'전국'만 고르거나, 지역을 직접 골라 주세요.",
    "C09_conversion_event": "전환 이벤트를 목표에 맞는 것으로 바꿔 주세요.",
    "C10_unsupported_category": "이 업종은 이 플랫폼에서 광고할 수 없어서 설정을 이어갈 수 없어요.",
    "C11_unverified_advertiser": "광고주 인증을 먼저 마쳐야 해요. 인증을 마치면 이어서 설정할 수 있어요.",
    "C13_placement": "노출 위치를 하나 이상 고르고, 노출 목표에서는 검색 지면을 빼 주세요.",
}
ISSUE_KEYWORD = {
    "C01_min_daily_budget": "최소", "C02_budget_inconsistent": "총 예산", "C03_bid_strategy": "입찰", "C04_adset_budget": "세트", "C05_dates": "시작일|종료일|날짜",
    "C06_age": "연령", "C06_age_restricted": "19세", "C07_regions": "전국", "C09_conversion_event": "전환", "C10_unsupported_category": "광고할 수 없",
    "C11_unverified_advertiser": "인증", "C13_placement": "노출 위치|지면",
}


def _won(n: int) -> str:
    return f"{n:,}원"


def display(path: str, value: Any) -> str:
    if path == "objective":
        return OBJECTIVE_KO[value]
    if path == "ad_category":
        return CATEGORY_KO[value]
    if path == "conversion_event":
        return EVENT_KO[value]
    if path == "bid_strategy":
        return BID_KO[value]
    if path in ("daily_budget_krw", "total_budget_krw", "target_cpa_krw", "max_cpc_krw"):
        return _won(value)
    if path.startswith("audience.age"):
        return f"{value}세"
    if path == "audience.genders":
        return "남녀 모두" if not value else "·".join(GENDER_KO[g] for g in value)
    if path == "placements":
        return "·".join(PLACE_KO[p] for p in value)
    if isinstance(value, list):
        return "·".join(map(str, value))
    return str(value)


# --------------------------------------------------------------------------- 에이전트
class Agent:
    def __init__(self, client: LLMClient, *, polish: bool = False) -> None:
        self.client = client
        self.polish = polish
        self.extract_task = extract_task()
        self.reply_task = reply_task()

    # ---- 1·2: 추출 + 근거 검사
    def understand(self, s: Session, utterance: str) -> tuple[str, list[dict[str, Any]], list[str], list[Rejected], int, str | None]:
        user = extract_input(today=s.today.isoformat(), weekday=WEEKDAYS[s.today.weekday()] + "요일", known=s.known(), last_assistant=s.last_assistant, utterance=utterance)
        try:
            out = self.client.call(self.extract_task, user).response
        except LLMError as exc:   # fail-closed: 추출 실패는 '아무것도 못 알아들음' — 상태를 바꾸지 않고 다시 말해 달라고 한다
            return "provide", [], [], [], 0, type(exc).__name__
        items = [i for i in out.get("slots", []) if isinstance(i, dict)]
        ok, bad = ground(items, utterance)
        retract = [p for p in out.get("retract", []) if p in SLOTS]
        return str(out.get("intent", "provide")), ok, retract, bad, len(items), None

    # ---- 한 턴
    def step(self, s: Session, utterance: str) -> TurnResult:
        s.turn += 1
        intent, accepted, retract, rejected, proposed, err = self.understand(s, utterance)
        changed = False
        for a in accepted:
            old = s.slots.get(a["path"])
            if old is None or old.value != a["value"] or old.source == "assumed":
                changed = True
            s.slots[a["path"]] = SlotValue(a["value"], "derived" if a["derived"] else "user", a["quote"], s.turn)
        for path in retract:
            if path in s.slots and s.slots[path].source != "assumed":
                del s.slots[path]
                changed = True
        res = self.respond(s, intent, accepted, utterance, changed=changed)
        res.rejected, res.proposed, res.llm_error = rejected, proposed, err
        return res

    def respond(self, s: Session, intent: str, accepted: list[dict[str, Any]], utterance: str, *, changed: bool) -> TurnResult:
        """상태(이미 갱신됨)에서 검증·정책·답변을 만든다. 추출과 무관하게 *상태만으로* 답이 정해진다(교사 강제 평가가 이 함수로 직전 답변을 재현한다)."""
        if changed:
            s.summary_shown = s.confirmed = False
        spec = s.effective_spec()
        miss = missing(s.user_spec())
        issues = validate(spec, today=s.today, verified=s.verified, adsets=plan_adsets(spec) if not miss else None, confirmed_high_budget=s.confirmed_high_budget)
        errors = [i for i in issues if i.severity == "error"]
        complete = not miss and not errors
        # 확인: 직전 답변이 요약이었고, 이번 발화에 값 변경이 없고, 광고주가 확인했고, 지금도 완결(오류·누락 없음)일 때만
        if intent == "confirm" and s.summary_shown and not changed and complete:
            s.confirmed = True
            if any(i.code == "C12_high_budget_confirm" for i in issues):
                s.confirmed_high_budget = True
                issues = validate(spec, today=s.today, verified=s.verified, adsets=plan_adsets(spec), confirmed_high_budget=True)
        elif intent in ("deny", "correction") and s.summary_shown:
            s.summary_shown = s.confirmed = False
        status = self._status(s, miss, errors, complete)
        questions = [] if status in ("refused", "ready") else self._questions(s, miss, errors)
        plan = self._plan(s, intent, status, issues, accepted, questions, utterance)
        message, fallback = self._compose(plan)
        s.last_assistant = message
        s.summary_shown = status == "awaiting_confirm"
        for q in questions:
            s.asked[q] = s.asked.get(q, 0) + 1
        return TurnResult(
            s.turn, utterance, intent, status, message, accepted, [], issues, questions, 0, fallback, None,
            {p: v.value for p, v in s.slots.items()},
        )

    # ---- 정책
    @staticmethod
    def _status(s: Session, miss: list[str], errors: list[Issue], complete: bool) -> Status:
        if any(i.code == "C10_unsupported_category" for i in errors):
            return "refused"
        if errors:
            return "invalid"
        if miss:
            return "needs_input"
        return "ready" if s.confirmed else "awaiting_confirm"

    @staticmethod
    def _questions(s: Session, miss: list[str], errors: list[Issue]) -> list[str]:
        """우선순위순으로 최대 둘. 같은 것을 두 번 물었는데도 비어 있으면 다음 것으로 넘어간다(같은 질문을 되풀이하지 않는다)."""
        fresh = [p for p in miss if s.asked.get(p, 0) < 2]
        return fresh[: MAX_QUESTIONS - (1 if errors else 0)]

    def _plan(self, s: Session, intent: str, status: Status, issues: list[Issue], accepted: list[dict[str, Any]], questions: list[str], utterance: str) -> dict[str, Any]:
        plan: dict[str, Any] = {"status": status, "off_topic": intent in ("off_topic", "injection"), "first_turn": s.turn == 1}
        plan["ack"] = [f"{LABEL[a['path']]}: {display(a['path'], a['value'])}" for a in accepted]
        plan["issues"] = [
            {"code": i.code, "severity": i.severity, "path": i.path, "message": i.message, "help": ISSUE_HELP.get(i.code, "")}
            for i in issues if i.severity == "error" or (i.code == "C12_high_budget_confirm")
        ]
        plan["questions"] = [{"slot": q, "text": QUESTION[q][0]} for q in questions if q in QUESTION]
        if intent == "question":
            plan["faq"] = self._faq(s)
        if status in ("awaiting_confirm", "ready"):
            plan["summary"] = self._summary(s)
        if status == "needs_input" and not questions:
            plan["blocked"] = [LABEL[p] for p in missing(s.user_spec())]
        return plan

    @staticmethod
    def _faq(s: Session) -> str:
        obj = s.slots.get("objective")
        if obj:
            return f"{OBJECTIVE_KO[obj.value]} 목표의 일 예산은 최소 {_won(MIN_DAILY[obj.value])}부터 가능해요."
        return "일 예산 최소 금액은 목표별로 달라요: " + ", ".join(f"{OBJECTIVE_KO[k]} {_won(v)}" for k, v in MIN_DAILY.items()) + "."

    def _summary(self, s: Session) -> dict[str, Any]:
        spec = s.effective_spec()
        rows: list[dict[str, Any]] = []
        order = (
            "objective", "ad_category", "daily_budget_krw", "total_budget_krw", "start_date", "end_date", "landing_url", "app_id", "conversion_event",
            "audience.age_min", "audience.age_max", "audience.genders", "audience.regions", "audience.interests", "bid_strategy", "target_cpa_krw", "max_cpc_krw", "placements",
        )
        for p in order:
            if p in spec:
                sv = s.slots.get(p)
                rows.append({"label": LABEL[p], "value": display(p, spec[p]), "assumed": sv is None})
        sets = plan_adsets(spec)
        return {
            "rows": rows,
            "adsets": [{"name": a.name, "share": int(a.budget_share * 100), "daily": a.daily_budget_krw} for a in sets],
            "high_budget": spec.get("daily_budget_krw", 0) > HIGH_BUDGET,
            "ready": s.confirmed,
        }

    # ---- 답변
    def _compose(self, plan: dict[str, Any]) -> tuple[str, bool]:
        text = template_message(plan)
        if not self.polish:
            return text, False
        try:
            msg = str(self.client.call(self.reply_task, reply_input({**plan, "draft": text})).response.get("message", ""))
        except LLMError:
            return text, True
        return (msg, False) if verify_reply(msg, plan, text) else (text, True)


def template_message(plan: dict[str, Any]) -> str:
    parts: list[str] = []
    if plan.get("first_turn"):
        parts.append("안녕하세요, 광고 캠페인 설정을 도와드릴게요.")
    if plan.get("off_topic"):
        parts.append("그 요청은 캠페인 설정과 관련이 없어서 도와드리기 어려워요(심사 결과는 보장해 드릴 수 없어요). 설정을 이어갈게요.")
    if plan.get("ack"):
        parts.append("확인했어요 — " + ", ".join(plan["ack"]) + ".")
    if plan.get("faq"):
        parts.append(plan["faq"])
    for i in plan.get("issues", []):
        if i["code"] == "C12_high_budget_confirm":
            continue
        parts.append(f"{i['message']} {i['help']}".strip())
    if plan["status"] == "refused":
        return "\n".join(parts)
    if plan.get("questions"):
        parts.append("\n".join(q["text"] for q in plan["questions"]))
    if plan.get("blocked"):
        parts.append("다음 정보가 있어야 집행할 수 있어요: " + ", ".join(plan["blocked"]) + ". 준비되면 알려 주세요.")
    summ = plan.get("summary")
    if summ:
        lines = ["지금까지 정리한 캠페인이에요."] if plan["status"] == "awaiting_confirm" else ["캠페인 설정이 확인됐어요."]
        for r in summ["rows"]:
            lines.append(f"- {r['label']}: {r['value']}" + (" (가정)" if r["assumed"] else ""))
        if summ["adsets"]:
            lines.append("- 광고 세트: " + " · ".join(f"{a['name']} {a['share']}% {_won(a['daily'])}" for a in summ["adsets"]))
        if summ["high_budget"]:
            lines.append(f"※ 일 예산이 {_won(HIGH_BUDGET)}을 넘는 큰 금액이에요. 이 금액이 맞다면 확인해 주세요.")
        if plan["status"] == "awaiting_confirm":
            lines.append("'가정'으로 표시한 값은 제가 기본값을 넣은 거예요. 이대로 진행할까요?")
        else:
            lines.append("심사는 별도로 진행되며 통과를 보장하지는 않아요.")
        parts.append("\n".join(lines))
    return "\n".join(p for p in parts if p)


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def verify_reply(message: str, plan: dict[str, Any], draft: str) -> bool:
    """LLM 이 쓴 답변이 계획을 벗어나지 않았는가. 하나라도 어긋나면 템플릿을 쓴다."""
    if not message.strip() or len(message) > 900 or FORBIDDEN_REPLY.search(message):
        return False
    allowed = {n.replace(",", "") for n in _NUM.findall(draft)} | {n.replace(",", "") for n in _NUM.findall(json.dumps(plan, ensure_ascii=False))}
    if any(n.replace(",", "") not in allowed for n in _NUM.findall(message)):
        return False
    for q in plan.get("questions", []):
        if not re.search(QUESTION[q["slot"]][1], message):
            return False
    for i in plan.get("issues", []):
        if i["code"] in ISSUE_KEYWORD and i["severity"] == "error" and not re.search(ISSUE_KEYWORD[i["code"]], message):
            return False
    asks_confirm = "이대로 진행" in message
    if (plan["status"] == "awaiting_confirm") != asks_confirm:
        return False
    return True


__all__ = ["Agent", "Session", "SlotValue", "TurnResult", "Rejected", "ground", "injection_spans", "template_message", "verify_reply", "ADSET_MIN_DAILY"]
