"""부하·격리 리허설: 실제 HTTP 서버(uvicorn, 같은 프로세스 스레드)에 동시 접속자를 붙여 지연·처리량·오류를 재고, 시끄러운 이웃과 judge 장애가 다른 요청에 번지는지 본다.

**이 수치는 이 기계의 것이다**(서버와 클라이언트가 같은 프로세스·같은 파이썬 GIL, 루프백 네트워크). 운영 용량 계획이 아니라 '장치가 설계대로 움직이는가'를 보는 리허설이다 — 그래서 결과 산출물에는
기계 정보를 같이 남기고, 문서는 SLO 충족 여부와 *상대적 변화*(이웃이 시끄러울 때 몇 배 느려지는가, 회로가 열리면 몇 배 빨라지는가)를 읽는다.
"""

from __future__ import annotations

import os
import platform
import statistics
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..api import create_app
from ..eval.dataset import Example
from ..judge import Judge, ProposedFinding
from ..settings import DEV_TOKENS, Settings

ALICE = {"Authorization": "Bearer dev-advertiser"}
CAROL = {"Authorization": "Bearer dev-advertiser-2"}


class LiveServer:
    """uvicorn 을 스레드에서 띄우고 임시 포트를 받는다."""

    def __init__(self, settings: Settings) -> None:
        import uvicorn

        self.app = create_app(settings)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = ""

    def __enter__(self) -> LiveServer:
        self.thread.start()
        for _ in range(500):
            if self.server.started and self.server.servers:
                port = self.server.servers[0].sockets[0].getsockname()[1]
                self.url = f"http://127.0.0.1:{port}"
                return self
            time.sleep(0.01)
        raise RuntimeError("서버가 시작하지 않았다")

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(5)


@dataclass
class Stats:
    latencies_ms: list[float] = field(default_factory=list)
    statuses: Counter[int] = field(default_factory=Counter)
    conn_errors: int = 0
    seconds: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, ms: float, status: int | None) -> None:
        with self.lock:
            self.latencies_ms.append(ms)
            if status is None:
                self.conn_errors += 1
            else:
                self.statuses[status] += 1


def pct(xs: Sequence[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, int(q * len(s) + 0.999999) - 1))], 2)


def summarize(st: Stats) -> dict[str, Any]:
    n = len(st.latencies_ms)
    ok = sum(v for k, v in st.statuses.items() if k < 500)
    return {
        "requests": n, "seconds": round(st.seconds, 3), "throughput_rps": round(n / st.seconds, 1) if st.seconds else None,
        "p50_ms": pct(st.latencies_ms, 0.50), "p95_ms": pct(st.latencies_ms, 0.95), "p99_ms": pct(st.latencies_ms, 0.99),
        "mean_ms": round(statistics.fmean(st.latencies_ms), 2) if n else None,
        "status": {str(k): v for k, v in sorted(st.statuses.items())}, "conn_errors": st.conn_errors,
        "available": round((ok) / n, 5) if n else None,      # 5xx·연결 실패가 아닌 응답의 비율(SLI)
        "rate_limited": st.statuses.get(429, 0),
    }


def bodies(pool: Sequence[Example], n: int, prefix: str = "L") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n):
        c = pool[i % len(pool)].creative
        d = c.model_dump(mode="json")
        d["ad_id"] = f"{prefix}{i:06d}"
        out.append(d)
    return out


def run_clients(
    url: str, headers: dict[str, str], payloads: Sequence[dict[str, Any]], concurrency: int, *, pace_s: float = 0.0, stats: Stats | None = None,
    stop: threading.Event | None = None,
) -> Stats:
    """``payloads`` 를 ``concurrency`` 개 스레드가 나눠 보낸다. ``pace_s`` 가 있으면 요청 사이에 그만큼 쉰다(고정 속도 클라이언트)."""
    import httpx

    st = stats or Stats()
    idx = iter(range(len(payloads)))
    idx_lock = threading.Lock()

    def worker() -> None:
        with httpx.Client(base_url=url, headers=headers, timeout=30.0) as c:
            while not (stop and stop.is_set()):
                with idx_lock:
                    i = next(idx, None)
                if i is None:
                    return
                t0 = time.perf_counter()
                try:
                    status: int | None = c.post("/v1/reviews", json=payloads[i]).status_code
                except Exception:  # noqa: BLE001 — 연결 실패도 센다
                    status = None
                st.add((time.perf_counter() - t0) * 1000, status)
                if pace_s:
                    time.sleep(pace_s)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st.seconds = time.perf_counter() - t0
    return st


def load_settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"principals": dict(DEV_TOKENS), "dev": True, "rate_per_min": 10**9, "max_waiting_per_submitter": 10**9}
    base.update(kw)
    return Settings(**base)


def machine() -> dict[str, Any]:
    return {"cpus": os.cpu_count(), "python": platform.python_version(), "platform": platform.platform(), "note": "서버·클라이언트가 한 프로세스(루프백). 이 기계의 수치다."}


def rehearse_load(pool: Sequence[Example], levels: Sequence[int] = (1, 8, 32, 128), per_level: int = 600) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with LiveServer(load_settings()) as srv:
        run_clients(srv.url, ALICE, bodies(pool, 40, "W"), 4)      # 워밍업
        for c in levels:
            n = max(per_level, 10 * c)
            out[str(c)] = summarize(run_clients(srv.url, ALICE, bodies(pool, n, f"C{c}-"), c))
    return out


def rehearse_neighbor(pool: Sequence[Example], *, flood_threads: int = 32, flood_requests: int = 1500, steady_requests: int = 100) -> dict[str, Any]:
    """carol 이 일정한 속도로 보내는 동안 alice 가 최대 속도로 쏟아붓는다. alice 에게는 분당 호출 상한(300)이 걸려 있다."""
    steady_pace = 0.04           # 초당 약 25건 이하: 두 구간(혼자·이웃과 함께) 합쳐 200건 < carol 의 분당 상한 300
    with LiveServer(load_settings(rate_per_min=300)) as srv:
        alone = run_clients(srv.url, CAROL, bodies(pool, steady_requests, "S"), 1, pace_s=steady_pace)
        stop = threading.Event()
        flood_stats = Stats()
        flood = threading.Thread(target=run_clients, args=(srv.url, ALICE, bodies(pool, flood_requests, "F"), flood_threads), kwargs={"stats": flood_stats, "stop": stop})
        flood.start()
        time.sleep(0.3)
        noisy = run_clients(srv.url, CAROL, bodies(pool, steady_requests, "N"), 1, pace_s=steady_pace)
        stop.set()
        flood.join(60)
    a, b, f = summarize(alone), summarize(noisy), summarize(flood_stats)
    infl = (b["p95_ms"] / a["p95_ms"]) if a["p95_ms"] and b["p95_ms"] else None
    return {"steady_alone": a, "steady_during_flood": b, "flooder": f, "p95_inflation": round(infl, 2) if infl else None}


class HangingJudge:
    """늘 느리게 응답하는 judge(장애 주입)."""

    name = "hanging"

    def __init__(self, delay_s: float) -> None:
        self.delay_s, self.calls = delay_s, 0

    def propose(self, creative: Any, policy: Any, already: Sequence[dict[str, Any]] = ()) -> list[ProposedFinding]:
        self.calls += 1
        time.sleep(self.delay_s)
        return []


def rehearse_judge_outage(pool: Sequence[Example], *, delay_s: float = 1.0, timeout_s: float = 0.25, requests: int = 40) -> dict[str, Any]:
    """judge 가 계속 느릴 때 요청 지연: 보호 없음(시간 제한이 길다) 대 보호 있음(시간 제한 + 회로 차단기)."""
    out: dict[str, Any] = {"judge_delay_s": delay_s, "guard_timeout_s": timeout_s}
    arms: dict[str, Callable[[], Settings]] = {
        "no_judge": lambda: load_settings(),
        "unguarded": lambda: load_settings(judge=HangingJudge(delay_s), judge_timeout_s=delay_s * 10, judge_failure_threshold=10**9),
        "guarded": lambda: load_settings(judge=HangingJudge(delay_s), judge_timeout_s=timeout_s, judge_failure_threshold=3, judge_reset_s=600),
    }
    for name, make in arms.items():
        n = requests if name != "unguarded" else min(requests, 12)     # 보호 없는 쪽은 한 건에 delay 만큼 걸리므로 적게
        with LiveServer(make()) as srv:
            st = run_clients(srv.url, ALICE, bodies(pool, n, f"J{name[:2]}"), 1)
        row = summarize(st)
        row["first6_ms"] = [round(x) for x in st.latencies_ms[:6]]
        row["after_first6_p95_ms"] = pct(st.latencies_ms[6:], 0.95)
        row["outcomes_review_share"] = None
        out[name] = row
    return out


def slo_check(level: dict[str, Any], *, p95_ms: float = 250.0, p99_ms: float = 1000.0, availability: float = 0.999) -> dict[str, bool]:
    return {
        "p95": level["p95_ms"] is not None and level["p95_ms"] <= p95_ms,
        "p99": level["p99_ms"] is not None and level["p99_ms"] <= p99_ms,
        "availability": level["available"] is not None and level["available"] >= availability,
    }


def rehearse_all(pool: Sequence[Example]) -> dict[str, Any]:
    load = rehearse_load(pool)
    return {
        "machine": machine(), "load": load, "slo": {c: slo_check(v) for c, v in load.items()},
        "slo_targets": {"p95_ms": 250.0, "p99_ms": 1000.0, "availability": 0.999},
        "neighbor": rehearse_neighbor(pool), "judge_outage": rehearse_judge_outage(pool),
    }


__all__ = ["HangingJudge", "Judge", "LiveServer", "Stats", "machine", "rehearse_all", "rehearse_judge_outage", "rehearse_load", "rehearse_neighbor", "slo_check", "summarize"]
