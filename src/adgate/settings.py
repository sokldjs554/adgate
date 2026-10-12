"""서버 설정과 인증 토큰. 토큰이 하나도 없으면 시작하지 않는다(fail-closed)."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass, field
from typing import Literal

from .judge import Judge

Role = Literal["advertiser", "reviewer"]


@dataclass(frozen=True)
class Principal:
    identity: str
    role: Role
    # 플랫폼이 이 계정을 사업자 인증했는가. 소재가 스스로 적은 `advertiser.verified` 는 이 값을 넘을 수 없다(독립 리뷰 H6).
    verified: bool = False


# ADGATE_DEV=1 일 때만 켜지는 데모용 토큰. 운영에서는 ADGATE_TOKENS 로 대체한다. carol 은 인증되지 않은 광고주다.
DEV_TOKENS: dict[str, Principal] = {
    "dev-advertiser": Principal("alice", "advertiser", verified=True),
    "dev-advertiser-2": Principal("carol", "advertiser", verified=False),
    "dev-reviewer": Principal("bob", "reviewer", verified=True),
}


@dataclass
class Settings:
    db_path: str = ":memory:"
    principals: dict[str, Principal] = field(default_factory=dict)
    rate_per_min: int = 120
    max_waiting_per_submitter: int = 50  # 한 광고주가 검토 대기열을 가득 채우지 못하게
    max_body_bytes: int = 128 * 1024
    judge: Judge | None = None
    judge_timeout_s: float = 8.0       # judge 호출 하나를 기다리는 한도(초). 넘으면 포기하고 사람에게 넘긴다(ops/guard.py)
    judge_max_concurrent: int = 8      # 진행 중인 judge 호출 상한(벌크헤드)
    judge_failure_threshold: int = 5   # 연속 실패가 이만큼이면 회로를 연다
    judge_reset_s: float = 30.0        # 회로가 열린 뒤 시험 호출까지의 시간
    dev: bool = False

    def authenticate(self, token: str) -> Principal | None:
        found: Principal | None = None
        for known, principal in self.principals.items():  # 상수 시간 비교, 모든 토큰을 끝까지 본다
            if hmac.compare_digest(known.encode(), token.encode()):
                found = principal
        return found

    @classmethod
    def from_env(cls) -> Settings:
        principals: dict[str, Principal] = {}
        raw = os.environ.get("ADGATE_TOKENS", "")
        for item in filter(None, (x.strip() for x in raw.split(","))):
            token, role_spec, identity = item.split(":", 2)
            role, _, flag = role_spec.partition("+")  # 'advertiser+verified' = 사업자 인증을 마친 광고주
            if role not in ("advertiser", "reviewer") or flag not in ("", "verified"):
                raise ValueError(f"알 수 없는 역할: {role_spec}")
            principals[token] = Principal(identity, role, verified=flag == "verified")  # type: ignore[arg-type]
        dev = os.environ.get("ADGATE_DEV") == "1"
        if dev and principals:
            raise RuntimeError(
                "ADGATE_DEV=1 은 공개된 데모 토큰을 켭니다. 실제 ADGATE_TOKENS 와 함께 쓸 수 없습니다 — 둘 중 하나만 설정하세요."
            )
        if dev:
            principals.update(DEV_TOKENS)
        if not principals:
            raise RuntimeError("인증 토큰이 없습니다. ADGATE_TOKENS=token:role:identity,… 를 설정하거나 데모는 ADGATE_DEV=1 로 켜세요.")
        return cls(
            db_path=os.environ.get("ADGATE_DB", "var/adgate.sqlite3"),
            principals=principals,
            rate_per_min=int(os.environ.get("ADGATE_RATE_PER_MIN", "120")),
            max_waiting_per_submitter=int(os.environ.get("ADGATE_MAX_WAITING_PER_SUBMITTER", "50")),
            dev=dev,
        )
