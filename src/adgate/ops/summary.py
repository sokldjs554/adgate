"""운영 리허설 첫 접촉 산출물(``artifacts/history/ops_first_contact.json``, ``ops_load_first_contact.json``)의 요약. 문서가 표를 마커로 채울 수 있게 `{k, n, value, ci}` 와 중앙값을 미리 계산한다.

새로 재는 것이 아니다: 보존된 첫 접촉 산출물을 읽어 같은 수치를 다른 모양으로 낼 뿐이다.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from ..eval.metrics import ratio

ROOT = Path(__file__).resolve().parents[3]
INCIDENTS = ("hard_outage", "partial_degradation", "slow_burn", "brief_blip")


def _med(xs: list[int | None]) -> float | None:
    got = [x for x in xs if x is not None]
    return statistics.median(got) if got else None


def drift_summary(ops: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm, a in ops["drift"]["arms"].items():
        null = {f"v{vol}": ratio(v["with_alarm"], v["streams"]) for vol, v in a["null"].items()}
        cells: dict[str, Any] = {}
        for key, v in a["drift"].items():
            kind, vol, mag = key.split("/")
            cells.setdefault(kind, {}).setdefault(f"v{vol}", {})[f"m{round(float(mag) * 100):02d}"] = {
                "detected": ratio(v["detected"], v["streams"]), "delay_median": v["delay_median"], "early": ratio(v["early_false_alarm"], v["streams"]),
            }
        out[arm] = {"null": null, "cells": cells}
    return out


def shadow_summary(ops: dict[str, Any]) -> dict[str, Any]:
    rows = ops["shadow"]["rows"]
    out: dict[str, Any] = {
        r["candidate"]: {"kind": r["kind"], "allowed": ratio(r["allowed"], r["streams"]), "blocked": ratio(r["blocked"], r["streams"]), "right": ratio(r["right"], r["streams"]), "disagree_mean": r["disagree_mean"]}
        for r in rows
    }
    out["_all"] = {"right": ratio(sum(r["right"] for r in rows), sum(r["streams"] for r in rows))}
    out["_sample"] = ops["shadow"]["sample"]
    return out


def alerting_summary(ops: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in ops["alerting"]["rows"]:
        cell: dict[str, Any] = {"false_page": row["false_page_episodes"], "false_ticket": row["false_ticket_episodes"], "streams": row["streams"]}
        for inc in INCIDENTS:
            for kind in ("page", "ticket"):
                xs = row["incidents"][inc][kind]
                cell[f"{inc}_{kind}"] = {"alerted": ratio(sum(x is not None for x in xs), len(xs)), "delay_median": _med(xs)}
        out.setdefault(row["policy"], {})[f"rpm{int(row['rpm'])}"] = cell
    return out


def load_summary(load: dict[str, Any]) -> dict[str, Any]:
    r = load["results"]
    out: dict[str, Any] = {"machine": r["machine"], "levels": {}, "slo_met": {}}
    for c, v in r["load"].items():
        out["levels"][f"c{c}"] = {k: v[k] for k in ("requests", "throughput_rps", "p50_ms", "p95_ms", "p99_ms", "conn_errors", "available")} | {"errors_5xx": sum(n for s, n in v["status"].items() if int(s) >= 500)}
        out["slo_met"][f"c{c}"] = {k: int(b) for k, b in r["slo"][c].items()}
    n = r["neighbor"]
    out["neighbor"] = {
        "alone_p95_ms": n["steady_alone"]["p95_ms"], "flood_p50_ms": n["steady_during_flood"]["p50_ms"], "flood_p95_ms": n["steady_during_flood"]["p95_ms"],
        "flood_p99_ms": n["steady_during_flood"]["p99_ms"], "p95_inflation": n["p95_inflation"], "steady_errors": sum(c for s, c in n["steady_during_flood"]["status"].items() if int(s) != 201) + n["steady_during_flood"]["conn_errors"],
        "flooder_limited": ratio(n["flooder"]["rate_limited"], n["flooder"]["requests"]),
    }
    j = r["judge_outage"]
    out["judge_outage"] = {
        "judge_delay_s": j["judge_delay_s"], "guard_timeout_s": j["guard_timeout_s"],
        **{name: {"p50_ms": j[name]["p50_ms"], "p95_ms": j[name]["p95_ms"], "after_first6_p95_ms": j[name]["after_first6_p95_ms"], "first3_ms": j[name]["first6_ms"][:3], "ok": j[name]["status"].get("201", 0), "requests": j[name]["requests"]} for name in ("no_judge", "unguarded", "guarded")},
    }
    return out


def shadow_flips(pool: list[Any] | None = None) -> dict[str, Any]:
    """사후 분석(사전등록 아님): 첫 접촉에서 게이트가 통과시킨 `no_challenge` 후보가 풀 전체에서 무엇을 바꾸는가."""
    from ..config import ReviewConfig
    from .shadow import cost, engine_of
    from .suite import pool as load_pool

    items = pool if pool is not None else load_pool()
    prod, cand = engine_of(ReviewConfig()), engine_of(ReviewConfig().without("challenge"))
    flips = [(e, prod(e), cand(e)) for e in items if prod(e) != cand(e)]
    return {
        "pool": len(items), "flips": len(flips),
        "gold_reject_review_to_approve": sum(e.gold.decision == "REJECT" and a == "REVIEW" and b == "APPROVE" for e, a, b in flips),
        "unsafe_approvals_added": sum(e.gold.decision != "APPROVE" and a != "APPROVE" and b == "APPROVE" for e, a, b in flips),
        "exact_prod_wrong": sum(a != e.gold.decision for e, a, _ in flips), "exact_cand_wrong": sum(b != e.gold.decision for e, _, b in flips),
        "safety_cost_prod": sum(cost(e.gold.decision, a, "safety") for e, a, _ in flips), "safety_cost_cand": sum(cost(e.gold.decision, b, "safety") for e, _, b in flips),
    }


def summarize(ops_path: Path | None = None, load_path: Path | None = None) -> dict[str, Any]:
    ops = json.loads((ops_path or ROOT / "artifacts/history/ops_first_contact.json").read_text(encoding="utf-8"))["results"]
    load = json.loads((load_path or ROOT / "artifacts/history/ops_load_first_contact.json").read_text(encoding="utf-8"))
    return {"drift": drift_summary(ops), "shadow": shadow_summary(ops), "alerting": alerting_summary(ops), "load": load_summary(load), "shadow_flips": shadow_flips()}
