"""REST API. 심사는 동기로 끝나고, 사람이 봐야 하는 건(REVIEW)은 ``waiting_input`` 으로 멈춰 resume 을 기다린다.

역할: advertiser(제출·본인 건 조회·수정안), reviewer(전체 조회·resume). 자기 소재는 스스로 결정할 수 없다(직무 분리).
오류는 RFC 9457 problem+json. 남의 건은 403 이 아니라 404(존재 여부를 알려 주지 않는다).
"""

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import ReviewConfig
from .models import Creative, ReviewResult
from .observability import Registry
from .pipeline import review
from .policy import load_policy
from .rewrite import RewriteResult, rewrite
from .settings import Principal, Settings
from .store import Run, Store

WEB_DIR = Path(__file__).parent / "web"
IDEM_KEY = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


class Problem(Exception):
    def __init__(self, status: int, title: str, detail: str = "", headers: dict[str, str] | None = None) -> None:
        super().__init__(title)
        self.status, self.title, self.detail, self.headers = status, title, detail, headers or {}


def problem_response(status: int, title: str, detail: str = "", headers: dict[str, str] | None = None, **extra: Any) -> JSONResponse:
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["APPROVE", "REJECT"]
    reason: str = Field(min_length=5, max_length=500)


class RateLimiter:
    """신원별 1분 창 호출 수 제한(프로세스 안)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, who: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[who]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


def _hash_creative(creative: Creative) -> str:
    return hashlib.sha256(creative.model_dump_json().encode()).hexdigest()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.db_path != ":memory:":
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(settings.db_path)
    registry = Registry()
    registry.describe("adgate_reviews_total", "심사 건수(판정별)")
    registry.describe("adgate_findings_total", "발견 건수(규칙별)")
    registry.describe("adgate_resumes_total", "사람 결정 건수")
    registry.describe("adgate_review_latency_ms", "심사 처리 시간(ms)")
    cfg = ReviewConfig(judge=settings.judge)
    policy = load_policy()
    limiter = RateLimiter(settings.rate_per_min)

    app = FastAPI(title="adgate", version=__version__, docs_url="/docs", redoc_url=None)
    app.state.store = store
    app.state.registry = registry

    # ------------------------------------------------------------------ 오류와 보안 헤더
    @app.exception_handler(Problem)
    async def _problem(_: Request, exc: Problem) -> JSONResponse:
        return problem_response(exc.status, exc.title, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(x) for x in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        return problem_response(422, "요청 형식이 올바르지 않습니다", "입력 필드를 확인해 주세요.", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem_response(exc.status_code, str(exc.detail))

    @app.middleware("http")
    async def _guard(request: Request, call_next: Any) -> Response:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_body_bytes:
            return problem_response(413, "요청이 너무 큽니다", f"본문은 {settings.max_body_bytes} 바이트 이하여야 합니다.")
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        if request.url.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # ------------------------------------------------------------------ 인증
    def current(authorization: Annotated[str | None, Header()] = None) -> Principal:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise Problem(401, "인증이 필요합니다", "Authorization: Bearer <token> 헤더가 필요합니다.", {"WWW-Authenticate": "Bearer"})
        who = settings.authenticate(authorization[7:].strip())
        if who is None:
            raise Problem(401, "토큰이 올바르지 않습니다", headers={"WWW-Authenticate": "Bearer"})
        if not limiter.allow(who.identity):
            raise Problem(429, "요청이 너무 많습니다", "잠시 후 다시 시도해 주세요.", {"Retry-After": "60"})
        return who

    Me = Annotated[Principal, Depends(current)]

    def visible(run: Run | None, me: Principal) -> Run:
        if run is None or (me.role != "reviewer" and run.submitter != me.identity):
            raise Problem(404, "찾을 수 없습니다", "해당 심사 건이 없습니다.")
        return run

    def view(run: Run, me: Principal) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": run.id, "state": run.state, "created_at": run.created_at, "outcome": run.result["outcome"],
            "final_outcome": run.final_outcome, "decided_by": run.decided_by, "result": run.result, "creative": run.creative,
        }
        if me.role == "reviewer":
            out["submitter"] = run.submitter
        return out

    # ------------------------------------------------------------------ 엔드포인트
    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "policy_version": policy.version, "dev": settings.dev}

    @app.get("/v1/me")
    def whoami(me: Me) -> dict[str, str]:
        return {"identity": me.identity, "role": me.role}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        waiting = float(store.count_by_state().get("waiting_input", 0))
        return registry.render({"adgate_waiting_input": {(): waiting}})

    @app.get("/v1/policy")
    def get_policy() -> dict[str, Any]:
        return {
            "version": policy.version,
            "priority": list(policy.priority),
            "rules": {
                rid: {
                    "title": r.title,
                    "applies_to": list(r.applies_to) if r.applies_to else "all",
                    "codes": {c: {"severity": s.severity, "message": s.message, "fix": s.fix} for c, s in r.codes.items()},
                }
                for rid, r in policy.rules.items()
            },
        }

    @app.post("/v1/reviews", status_code=201)
    def submit(
        creative: Creative, me: Me, response: Response, idempotency_key: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if idempotency_key is not None and not IDEM_KEY.match(idempotency_key):
            raise Problem(422, "Idempotency-Key 형식이 올바르지 않습니다", "영문·숫자·_.:- 128자 이하만 쓸 수 있습니다.")
        req_hash = _hash_creative(creative)

        def replay(existing: Run) -> dict[str, Any]:
            if existing.request_hash != req_hash:
                raise Problem(409, "같은 Idempotency-Key 로 다른 요청이 들어왔습니다", "키를 바꾸거나 같은 본문을 보내 주세요.")
            response.status_code = 200
            response.headers["Idempotent-Replayed"] = "true"
            return view(existing, me)

        if idempotency_key:
            found = store.find_idempotent(me.identity, idempotency_key)
            if found:
                return replay(found)

        trace: list[dict[str, Any]] = []
        result: ReviewResult = review(creative, cfg, policy, trace)
        waiting = result.outcome == "REVIEW"
        events: list[tuple[str, dict[str, Any]]] = [("submitted", {"ad_id": creative.ad_id, "category": creative.advertiser.category})]
        events += [("stage", t) for t in trace]
        events.append(("waiting_input" if waiting else "decided", {"outcome": result.outcome, "rules": sorted({f.rule_id for f in result.findings if f.severity != "info"})}))
        try:
            run = store.create_run(
                submitter=me.identity, creative=json.loads(creative.model_dump_json()), result=json.loads(result.model_dump_json()),
                state="waiting_input" if waiting else "completed", final_outcome=result.outcome,
                decided_by="pending" if waiting else "auto", idempotency_key=idempotency_key, request_hash=req_hash, events=events,
            )
        except sqlite3.IntegrityError:
            existing = store.find_idempotent(me.identity, idempotency_key or "")
            if existing is None:
                raise
            return replay(existing)

        registry.inc("adgate_reviews_total", outcome=result.outcome)
        registry.observe_ms("adgate_review_latency_ms", result.stats.latency_ms)
        for f in result.findings:
            if f.severity != "info":
                registry.inc("adgate_findings_total", rule=f.rule_id)
        response.headers["Location"] = f"/v1/reviews/{run.id}"
        return view(run, me)

    @app.get("/v1/reviews")
    def list_reviews(me: Me, state: str | None = None, limit: int = 50) -> dict[str, Any]:
        if state is not None and state not in ("completed", "waiting_input", "failed"):
            raise Problem(422, "state 값이 올바르지 않습니다")
        limit = max(1, min(limit, 200))
        runs = store.list_runs(submitter=None if me.role == "reviewer" else me.identity, state=state, limit=limit)
        return {"items": [view(r, me) for r in runs]}

    @app.get("/v1/reviews/{run_id}")
    def get_review(run_id: str, me: Me) -> dict[str, Any]:
        return view(visible(store.get(run_id), me), me)

    @app.get("/v1/reviews/{run_id}/events")
    def get_events(run_id: str, me: Me) -> dict[str, Any]:
        run = visible(store.get(run_id), me)
        return {"run_id": run.id, "events": store.events(run.id)}

    @app.post("/v1/reviews/{run_id}/rewrite")
    def post_rewrite(run_id: str, me: Me) -> RewriteResult:
        run = visible(store.get(run_id), me)
        creative = Creative.model_validate(run.creative)
        result = ReviewResult.model_validate(run.result)
        out = rewrite(creative, result, cfg, policy)
        store.add_event(run.id, "rewrite_requested", {"status": out.status, "by": me.identity, "applied": len(out.applied)})
        return out

    @app.post("/v1/reviews/{run_id}/resume")
    def resume(run_id: str, body: ResumeRequest, me: Me) -> dict[str, Any]:
        run = visible(store.get(run_id), me)
        if me.role != "reviewer":
            raise Problem(403, "권한이 없습니다", "심사자만 사람 검토 결과를 입력할 수 있습니다.")
        if run.submitter == me.identity:
            raise Problem(403, "직무 분리 위반", "자기가 제출한 소재는 스스로 결정할 수 없습니다.")
        if run.state != "waiting_input":
            raise Problem(409, "이미 결정된 건입니다", f"현재 상태: {run.state}")
        if not store.resume(run.id, verdict=body.verdict, reviewer=me.identity, reason=body.reason):
            raise Problem(409, "이미 결정된 건입니다", "다른 심사자가 먼저 결정했습니다.")
        registry.inc("adgate_resumes_total", verdict=body.verdict)
        updated = store.get(run.id)
        assert updated is not None
        return view(updated, me)

    # ------------------------------------------------------------------ 콘솔
    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def console() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app
