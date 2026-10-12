"""소재 생성 파이프라인(오케스트레이터).

    페이지 파싱(숨긴 텍스트 제거) → 정화(주입 문장 휴리스틱) → 이미지 읽기(VLM) → 사실 추출(LLM, 인용 검증) → 사실 심사(정책)
      → 후보 생성(LLM) → [후보마다] 결정론 검증 + 정책 심사 → 막히면 수리(LLM) 를 최대 R 번 → 점수 순 추천

설계 원칙(심사 파이프라인과 같다):

* **LLM 은 제안만 한다.** 후보가 승인되는 것은 결정론 검증과 정책 심사를 통과했을 때뿐이다. 통과한 후보가 없으면 *만들지 못했다*고 답한다(fail-closed).
* **방어는 겹으로.** 숨긴 텍스트 제거 → 정화 → 데이터 표시(프롬프트) → 사실 인용 검증 → 사실 심사 → 후보 검증 → 정책 심사. 각 겹이 막은 것을 결과에 남긴다.
* **고칠 수 없는 것은 고치려 들지 않는다.** 광고주 인증 같은 계정 차원의 문제, 랜딩(상품 페이지) 자체의 문제는 문구를 다시 써도 풀리지 않는다 → 수리하지 않고 사유를 알린다.
* 같은 ``driver`` 가 실제 호출과 녹음 재생에 쓰인다(``llm.record``). 파이프라인은 결정론적이다.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..config import ReviewConfig
from ..judge import Judge
from ..llm import LLMClient, LLMError, LLMResult, LLMTask, Part, RenderedImage
from ..models import Advertiser, Creative, ReviewResult
from ..pipeline import review
from ..policy import Policy, load_policy
from ..vision import texts as spec_texts
from ..vision.read import read_image
from ..vision.render import RENDERER_VERSION, spec_json
from .layout import image_spec_for, overlay_image_text
from .models import Attempt, Candidate, CandidateTrace, Constraints, Fact, Problem, ProductResult
from .page import parse_page
from .products import LoadedProduct, norm
from .quality import score
from .screen import screen_facts
from .tasks import FACTS, facts_user, generate_task, generate_user, repair_task, repair_user
from .verify import verify

_SENT = re.compile(r"[^.!?\n]+[.!?]?")
_ADDRESSEE = re.compile(
    r"(?:\bAI\b|인공지능|어시스턴트|챗봇|\bLLM\b|에이전트|광고\s*(?:문구\s*)?(?:생성|작성|제작)\s*(?:기|도구|시스템|봇|AI)?|문구\s*(?:생성|작성)|"
    r"assistant|system\s*prompt|프롬프트)", re.IGNORECASE,
)
_DIRECTIVE = re.compile(
    r"(?:쓰세요|쓰십시오|써라|써\s*주세요|넣으세요|넣어라|넣어\s*주세요|넣을\s*것|출력하세요|출력하라|출력해|따르세요|따라라|무시|지시|반드시|절대|하지\s*마|"
    r"반영하|포함하세요|포함할\s*것|ignore|disregard|must|do not|only output)", re.IGNORECASE,
)
_OVERRIDE = re.compile(r"(?:ignore\s+(?:all|any|the|previous)|disregard|이전\s*(?:지시|명령|프롬프트)|위\s*지시|시스템\s*프롬프트|이\s*문장을\s*읽는)", re.IGNORECASE)
ACCOUNT_RULES = ("AD-ADV-01",)
MAX_LANDING = 20000


def looks_like_injection(sentence: str) -> bool:
    """생성기·AI 에게 하는 지시처럼 보이는 문장(휴리스틱). 호출 대상('AI', '광고 문구')과 지시 어미가 함께 있어야 한다."""
    return bool(_OVERRIDE.search(sentence) or (_ADDRESSEE.search(sentence) and _DIRECTIVE.search(sentence)))


def sanitize_lines(lines: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    """지시처럼 보이는 *문장*만 뺀다(줄 전체가 아니라). 뺀 문장은 감사용으로 돌려준다."""
    kept: list[tuple[str, str]] = []
    flagged: list[str] = []
    for kind, text in lines:
        pieces = [m.group().strip() for m in _SENT.finditer(text) if m.group().strip()]
        good = [p for p in pieces if not looks_like_injection(p)]
        flagged += [p for p in pieces if looks_like_injection(p)]
        if good:
            kept.append((kind, " ".join(good)))
    return kept, flagged


@dataclass
class CreativeConfig:
    constraints: Constraints = field(default_factory=Constraints)
    sanitize: bool = True        # 지시처럼 보이는 문장을 페이지에서 뺀다
    screen: bool = True          # 사실 하나하나를 정책에 통과시켜 위반 소지가 있는 사실을 거른다
    gate: bool = True            # False: 생성만(검증·심사·수리 없음) — '게이트가 없으면 어떻게 되는가'를 재는 대조군
    use_landing: bool = True     # 상품 페이지를 랜딩으로 보고 심사한다(광고-랜딩 대조·랜딩 속 위반은 사람 검토)
    read_image: bool = True      # False: 상품 이미지 사양의 글자를 그대로 쓴다(오라클)
    render_check: bool = False   # 추천 1순위를 그려 다시 읽어 본다(왕복 검사)
    judge: Judge | None = None   # 심사 게이트 안의 LLM judge(선택)


class CountingClient:
    """호출 수를 과제별로 센다(스레드 안전). 결과 비용 표에 쓴다."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "client")
        self.counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def call(self, task: LLMTask, user: str | Sequence[Part]) -> LLMResult:
        with self._lock:
            self.counts[task.name] = self.counts.get(task.name, 0) + 1
        return self.inner.call(task, user)


def _ground(raw: list[dict[str, Any]], page_plain: str, image_plain: str) -> tuple[list[Fact], int]:
    """추출한 사실의 인용이 원문에 글자 그대로 있어야 사실이 된다. 없으면 버리고 센다."""
    page_n, image_n = norm(page_plain), norm(image_plain)
    out: list[Fact] = []
    dropped = 0
    seen: set[str] = set()
    for r in raw:
        text = norm(str(r.get("text", "")))
        in_page, in_image = text in page_n, text in image_n
        if not text or not (in_page or in_image) or text in seen:
            dropped += bool(text) and text not in seen
            continue
        seen.add(text)
        try:
            out.append(Fact(
                id=f"f{len(out) + 1}", text=text, source="page" if in_page else "image", kind=r.get("kind", "other"),
                importance=r.get("importance", 2),
            ))
        except ValidationError:
            dropped += 1
    return out, dropped


def _creative(c: Candidate, advertiser: Advertiser, image_text: str, landing: str | None, ad_id: str) -> Creative:
    return Creative(
        ad_id=ad_id, advertiser=advertiser, headline=c.headline, body=c.body, cta=c.cta, disclosure=c.disclosure,
        image_text=image_text or None, landing_text=landing,
    )


def _review_problems(result: ReviewResult) -> tuple[list[Problem], list[str], bool]:
    """심사 결과 → (문구로 고칠 수 있는 사유, 랜딩에서 나온 규칙, 계정 차원의 문제가 있는가)."""
    problems: list[Problem] = []
    landing: list[str] = []
    account = False
    for r in result.reasons:
        if r.severity == "info":
            continue
        if r.rule_id in ACCOUNT_RULES or (r.rule_id == "AD-BRD-01" and r.field is None):
            account = True
        elif r.field == "landing_text":
            landing.append(r.rule_id)
        else:
            problems.append(Problem(kind="policy", code=f"{r.rule_id}/{r.code}", message=r.message, quote=r.quote, fix=r.fix))
    return problems, sorted(set(landing)), account


def account_block(advertiser: Advertiser) -> str | None:
    """계정 차원에서 어떤 문구로도 승인될 수 없는 경우의 사유(생성 전에 알린다)."""
    if advertiser.category in ("adult", "gambling"):
        return "restricted_category"
    if advertiser.category in ("loan", "invest") and not advertiser.verified:
        return "unverified_advertiser"
    return None


def run_product(
    lp: LoadedProduct, client: LLMClient, policy: Policy | None = None, cfg: CreativeConfig | None = None, *, image_path: str | None = None,
) -> ProductResult:
    policy = policy or load_policy()
    cfg = cfg or CreativeConfig()
    cons = cfg.constraints.model_copy(update={"max_headline_chars": min(cfg.constraints.max_headline_chars, lp.product.brand_voice.max_headline_chars)})
    p = lp.product
    counter = CountingClient(client)
    res = ProductResult(product_id=p.id, advertiser=p.advertiser, brand_voice=p.brand_voice)

    reason = account_block(p.advertiser)
    if reason:
        res.status, res.error = "blocked_by_account", reason
        return res

    try:
        # 1) 페이지: 보이는 텍스트만, 지시 같은 문장은 뺀다
        doc = parse_page(lp.html)
        res.hidden_dropped = len(doc.dropped)
        lines = doc.lines()
        if cfg.sanitize:
            lines, res.injection_flagged = sanitize_lines(lines)
        page_plain = "\n".join(t for _, t in lines)
        page_lines = [f"[{k}] {t}" for k, t in lines]
        # 2) 상품 이미지 읽기
        if cfg.read_image:
            from ..llm import Image

            # 파일이 없으면 사양으로 그린 이미지를 쓴다 — 열쇠가 사양+렌더러 버전이라 Pillow/FreeType 이 달라도 녹음을 재생할 수 있다(실호출 때만 그린다)
            part = Image(image_path) if image_path is not None else RenderedImage(spec_json(p.image), renderer=RENDERER_VERSION)
            image_lines = [t.text for t in read_image(counter, part).texts if t.text.strip()]
        else:
            image_lines = spec_texts(p.image)
        res.image_texts = image_lines
        # 3) 사실 추출 + 인용 검증 + 사실 심사
        raw = counter.call(FACTS, facts_user(p.advertiser.category, page_lines, image_lines)).response
        facts, res.ungrounded_facts = _ground(list(raw.get("facts", [])), page_plain, "\n".join(image_lines))
        res.risky_claims = [str(x.get("text", "")) for x in raw.get("risky_claims", [])]
        res.facts = facts
        usable = [f for f in facts if f.kind != "review"]
        if cfg.screen:
            usable, res.screened_out = screen_facts(usable, p.advertiser, policy)
        if not usable:
            res.status, res.error = "no_safe_candidate", "no_usable_facts"
            return res
        by_id = {f.id: f for f in usable}
        # 4) 후보 생성
        gen = generate_task(policy, cons)
        raw_c = counter.call(gen, generate_user(p.advertiser, p.brand_voice, usable, cons)).response
        cands: list[Candidate] = []
        for item in raw_c.get("candidates", [])[: cons.n_candidates]:
            try:
                cands.append(Candidate.model_validate(item))
            except ValidationError:
                res.invalid_candidates += 1
        if not cands:
            res.status, res.error = "no_safe_candidate", "no_valid_candidates"
            return res
        # 랜딩 = 사용자가 상품 페이지에서 보는 것: 본문 + 상품 이미지에 인쇄된 글자(이미지에만 있는 사실을 쓴 후보가 '랜딩에 없는 주장'으로 반려되지 않게)
        landing = ("\n".join([page_plain, *image_lines])[:MAX_LANDING] if cfg.use_landing else None) or None
        review_cfg = ReviewConfig(judge=cfg.judge)
        repair = repair_task(policy, cons)

        # 5) 후보마다: 검증 + 심사 → 막히면 수리
        for n, c0 in enumerate(cands):
            attempts: list[Attempt] = []
            cur = c0
            final = "exhausted"
            for round_ in range(cons.repair_rounds + 1):
                itext = overlay_image_text(cur, p.image)
                creative = _creative(cur, p.advertiser, itext, landing, f"{p.id}-c{n}-r{round_}")
                if not cfg.gate:
                    attempts.append(Attempt(candidate=cur, problems=[], outcome="APPROVE", creative=creative))
                    final = "approved"
                    break
                problems = verify(cur, by_id, p.advertiser, p.brand_voice, cons, product_image=p.image, hidden_texts=doc.dropped)
                result = review(creative, review_cfg, policy)
                pol, landing_rules, account = _review_problems(result)
                problems += pol
                attempts.append(Attempt(candidate=cur, problems=problems, outcome=result.outcome, creative=creative))
                if account:
                    final = "blocked_by_account"
                    break
                if not problems:
                    final = "approved" if result.outcome == "APPROVE" else "landing_review"
                    break
                if round_ == cons.repair_rounds:
                    break
                try:
                    fixed = counter.call(repair, repair_user(
                        p.advertiser, p.brand_voice, usable, cur.model_dump(mode="json"), [x.model_dump(mode="json") for x in problems],
                    )).response
                    cur = Candidate.model_validate(fixed)
                except (LLMError, ValidationError):
                    attempts[-1].problems.append(Problem(kind="verify", code="repair_failed", message="수리 호출이 실패했거나 형식이 어긋났습니다."))
                    break
            res.traces.append(CandidateTrace(attempts=attempts, accepted=final in ("approved", "landing_review"), final_status=final))

        # 6) 추천: 승인된 후보를 점수 순으로, 그다음 랜딩 때문에 사람이 봐야 하는 후보
        ranked = sorted(
            (i for i, t in enumerate(res.traces) if t.final_status in ("approved", "landing_review")),
            key=lambda i: (res.traces[i].final_status != "approved", -score(res.traces[i].attempts[-1].candidate, by_id, p.brand_voice, cons)),
        )
        res.recommended = ranked
        statuses = {t.final_status for t in res.traces}
        if "approved" in statuses:
            res.status = "ok"
        elif "landing_review" in statuses:
            res.status = "landing_review"
        elif statuses == {"blocked_by_account"}:
            res.status = "blocked_by_account"
        else:
            res.status = "no_safe_candidate"
        # 7) 왕복 검사: 추천 1순위를 그려 다시 읽는다
        if cfg.render_check and ranked:
            best = res.traces[ranked[0]].attempts[-1].candidate
            spec = image_spec_for(best, p.image, cons)
            expected = spec_texts(spec)
            reading = read_image(counter, RenderedImage(spec_json(spec), renderer=RENDERER_VERSION))
            got = norm(" ".join(t.text for t in reading.texts))
            hit = [e for e in expected if norm(e) in got]
            res.render_checks.append({"trace": ranked[0], "expected": expected, "read": [t.text for t in reading.texts], "fidelity": round(len(hit) / len(expected), 4)})
    except LLMError as exc:
        res.status, res.error = "failed", type(exc).__name__
    res.calls = dict(counter.counts)
    return res
