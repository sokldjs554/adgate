"""프로세스 안 지표 레지스트리. Prometheus 텍스트 형식으로 내보낸다(외부 의존성 없음)."""

from __future__ import annotations

import threading
from collections import defaultdict

LATENCY_BUCKETS_MS = (1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 1000.0)


def _fmt(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + inner + "}"


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(lambda: defaultdict(float))
        self._hist: dict[str, dict[str, float]] = {}
        self._help: dict[str, str] = {}

    def describe(self, name: str, text: str) -> None:
        self._help[name] = text

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._counters[name][key] += value

    def observe_ms(self, name: str, value: float) -> None:
        with self._lock:
            h = self._hist.setdefault(name, {"sum": 0.0, "count": 0.0, **{f"le_{b}": 0.0 for b in LATENCY_BUCKETS_MS}})
            h["sum"] += value
            h["count"] += 1
            for b in LATENCY_BUCKETS_MS:
                if value <= b:
                    h[f"le_{b}"] += 1

    def render(self, gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] | None = None) -> str:
        lines: list[str] = []
        with self._lock:
            for name, series in sorted(self._counters.items()):
                if name in self._help:
                    lines.append(f"# HELP {name} {self._help[name]}")
                lines.append(f"# TYPE {name} counter")
                for labels, v in sorted(series.items()):
                    lines.append(f"{name}{_fmt(labels)} {v:g}")
            for name, h in sorted(self._hist.items()):
                if name in self._help:
                    lines.append(f"# HELP {name} {self._help[name]}")
                lines.append(f"# TYPE {name} histogram")
                for b in LATENCY_BUCKETS_MS:
                    lines.append(f'{name}_bucket{{le="{b:g}"}} {h[f"le_{b}"]:g}')
                lines.append(f'{name}_bucket{{le="+Inf"}} {h["count"]:g}')
                lines.append(f"{name}_sum {h['sum']:g}")
                lines.append(f"{name}_count {h['count']:g}")
        for name, series in sorted((gauges or {}).items()):
            lines.append(f"# TYPE {name} gauge")
            for labels, v in sorted(series.items()):
                lines.append(f"{name}{_fmt(labels)} {v:g}")
        return "\n".join(lines) + "\n"
