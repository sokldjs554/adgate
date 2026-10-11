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


# ADGATE_DEV=1 일 때만 켜지는 데모용 토큰. 운영에서는 ADGATE_TOKENS 로 대체한다.
DEV_TOKENS: dict[str, Principal] = {
    "dev-advertiser": Principal("alice", "advertiser"),
    "dev-advertiser-2": Principal("carol", "advertiser"),
    "dev-reviewer": Principal("bob", "reviewer"),
}


@dataclass
class Settings:
    db_path: str = ":memory:"
    principals: dict[str, Principal] = field(default_factory=dict)
    rate_per_min: int = 120
    max_body_bytes: int = 128 * 1024
    judge: Judge | None = None
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
            token, role, identity = item.split(":", 2)
            if role not in ("advertiser", "reviewer"):
                raise ValueError(f"알 수 없는 역할: {role}")
            principals[token] = Principal(identity, role)  # type: ignore[arg-type]
        dev = os.environ.get("ADGATE_DEV") == "1"
        if dev:
            principals.update(DEV_TOKENS)
        if not principals:
            raise RuntimeError("인증 토큰이 없습니다. ADGATE_TOKENS=token:role:identity,… 를 설정하거나 데모는 ADGATE_DEV=1 로 켜세요.")
        return cls(
            db_path=os.environ.get("ADGATE_DB", "var/adgate.sqlite3"),
            principals=principals,
            rate_per_min=int(os.environ.get("ADGATE_RATE_PER_MIN", "120")),
            dev=dev,
        )
