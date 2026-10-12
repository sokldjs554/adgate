"""개발용 시나리오(탐지기를 *조정*하는 데 쓴 것). 독립 작성자가 쓴 동결 셋(``data/insight/``)과 다르다 — 설계자가 쓰고 결과를 보며 고쳤으므로 holdout 이 아니다.

격자: 원인 × 크기 × 시드(잡음 수준은 시드로 돌려 가며), 사건 없는 시나리오(예산·잡음·요일 패턴 조합), 복합 사건 몇 쌍.
"""

from __future__ import annotations

from typing import Any

from .sim import IncidentScenario

SINGLE: tuple[tuple[str, float, dict[str, Any]], ...] = (
    ("tracking_outage", 0.9, {}), ("tracking_outage", 0.5, {}), ("landing_degradation", 0.5, {}), ("budget_cut", 0.5, {}), ("budget_cut", 0.2, {}),
    ("bid_drop", 0.4, {}), ("bid_drop", 0.2, {}), ("creative_fatigue", 0.6, {"creative": 1}), ("audience_saturation", 0.4, {}), ("audience_saturation", 0.2, {}),
    ("ad_rejected", 1.0, {"creative": 0}), ("seasonal_cpm_spike", 0.5, {"end_day": 24}),
)
COMBOS: tuple[list[tuple[str, int, int | None, float, dict[str, Any]]], ...] = (
    [("budget_cut", 20, None, 0.4, {}), ("tracking_outage", 24, None, 0.7, {})],
    [("creative_fatigue", 18, None, 0.7, {"creative": 0}), ("seasonal_cpm_spike", 28, 32, 0.5, {})],
    [("bid_drop", 20, None, 0.4, {}), ("landing_degradation", 27, None, 0.6, {})],
    [("ad_rejected", 21, None, 1.0, {"creative": 1}), ("audience_saturation", 26, None, 0.4, {})],
)


def _sc(i: str, seed: int, incidents: list[dict[str, Any]], *, noise: str = "mid", budget: int = 500_000, pattern: str = "weekday_heavy", days: int = 42) -> IncidentScenario:
    return IncidentScenario.model_validate({
        "id": i, "seed": seed, "days": days, "start_weekday": seed % 7,
        "campaign": {"noise_level": noise, "daily_budget_krw": budget, "weekly_pattern": pattern}, "incidents": incidents})


def dev_scenarios() -> list[IncidentScenario]:
    out: list[IncidentScenario] = []
    for cause, mag, extra in SINGLE:
        for seed in (1, 2, 3, 4, 5, 6):
            noise = ("low", "mid", "high")[seed % 3]
            out.append(_sc(f"dev-{cause}-{mag}-{seed}", seed, [{"cause": cause, "start_day": 20, "magnitude": mag, **extra}], noise=noise))
    for k, combo in enumerate(COMBOS):
        for seed in (1, 2, 3):
            incs = [{"cause": c, "start_day": s, "end_day": e, "magnitude": m, "primary": i == 0, **ex} for i, (c, s, e, m, ex) in enumerate(combo)]
            out.append(_sc(f"dev-combo{k}-{seed}", seed, incs, noise=("low", "mid")[seed % 2], budget=800_000, days=48))
    for budget in (30_000, 100_000, 500_000, 3_000_000):
        for noise in ("low", "mid", "high"):
            for pat in ("weekday_heavy", "weekend_heavy", "flat"):
                for seed in range(3):
                    out.append(_sc(f"dev-null-{budget}-{noise}-{pat}-{seed}", 5000 + seed * 17 + budget % 97, [], noise=noise, budget=budget, pattern=pat))
    return out
