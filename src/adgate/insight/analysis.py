"""성과 이상 탐지 → 원인 진단 → 액션. 전부 결정론이다(LLM 은 이 결과를 *설명*할 뿐 판정하지 않는다).

관측(``Observation``)은 실제 시스템이 보는 것 — 일별 지표, 소재별 지표, 변경 이력 — 만 담는다. 시뮬레이터의 기댓값·정답은 모른다.

**탐지** — 최근 ``W`` 일 창(3·7·14)의 합을 *깨끗한* 과거 창과 견준다.
  - 3일 창은 같은 요일로 맞춘 1·2·… 주 전 창, 7·14일 창은 이어진 깨끗한 창(7의 배수라 요일 구성이 같다)이 기준이다. 요일 패턴은 비교에서 상쇄된다.
  - 이미 경보가 난 날은 기준에 넣지 않아, 사건이 길어져도 기준선이 오염돼 '끝난 것처럼' 보이지 않는다.
  - 전환율·CTR 처럼 개수의 비율인 지표는 이항 z 검정(기준 창의 불확실성 포함)으로, 노출·지출·CPM 처럼 로그정규 잡음이 낀 지표는 지금까지 본 깨끗한
    창의 편차(MAD)에 약한 사전값을 섞어 표준오차를 잰다. 기준 창이 적을수록 표준오차를 √(1+1/k) 만큼 키운다.
  - 경보 조건: |z| ≥ 3.5 이고 상대 편차 ≥ 12% 인 창이 이틀 연속(또는 한 번에 30% 이상). 긴 창은 느리지만 작은 변화(전환이 적은 캠페인의 전환율 하락)를 잡는다.

**진단** — 사건 구간의 지표 편차 벡터(예산·소진율·CPM·빈도·CTR·전환율·이탈률·소재별 CTR/노출)를 원인별 *신호 서명*에 가중 최소제곱으로 맞춘다
  (예: 전환 측정 장애 = 전환율만 −m, 랜딩 악화 = 전환율 −m + 이탈률 +0.35m). 서명마다 크기 m 을 하나 맞추고 잔차(χ²)가 작은 쪽이 이긴다.
  변경 이력은 *단서*일 뿐이다: 서명이 이미 맞는 원인의 사전확률을 조금 올릴 뿐, 이력만으로 원인을 만들지 못한다(틀린 이력에 속지 않는다).
  원인 둘을 함께 맞춘 모형이 단일 원인보다 χ² 를 충분히 줄이면 복합 사건으로 본다.

**액션** — 원인 사후확률에 대한 기대효용으로 고른다(허용 +1, 해로움 −3). 원인이 갈리면 해로운 쪽을 피해 '기다리며 지켜보기'나 두 원인 모두에 무해한 점검을 권한다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .sim import ACCEPTABLE, BASE_UTIL, HARMFUL, Day, Event
from .sim import Cause as CauseName

REL_MIN = 0.12          # 상대 편차가 이보다 작으면 잡음이 아무리 작아도 경보하지 않는다(정답 이상일 기준 10% 보다 약간 높다)
Z_ALERT = 3.5
SINGLE_DAY = 0.30       # 한 번에 이만큼 벗어나면(|z| ≥ Z_ALERT+1) 이틀 연속을 기다리지 않는다
WINDOWS = (3, 7, 14)
K_REF = 4               # 기준 창은 최대 4개
DRIFT = 0.0025         # 하루당 허용하는 완만한 추세. 기준이 오래되면 그만큼 경보 문턱을 올린다
DEFF = 1.3              # 이항 분산의 과대분산 보정
N0 = 3.0                # 표준오차 사전값의 가상 표본 수
PRIOR_DAY = {"impressions": 0.12, "spend": 0.05, "cpm": 0.13, "budget": 0.005, "util": 0.045, "frequency": 0.04, "bounce": 0.02}
SUPPORT = ("util", "frequency", "bounce")
MODEL_ERR = {"budget": 0.02, "util": 0.02, "cpm": 0.03, "frequency": 0.05, "ctr": 0.02, "cvr": 0.03, "bounce": 0.02}
CREATIVE_ERR = 0.04


@dataclass
class Observation:
    days: list[Day]
    events: list[Event]

    @property
    def n_creatives(self) -> int:
        return len(self.days[0].creatives)


@dataclass
class Dev:
    value: float            # 상대 편차(비율형·로그정규 지표) 또는 절대 편차(소진율·빈도·이탈률)
    se: float
    k: int = 0              # 기준 창 수
    age: float = 0.0        # 기준 창들의 평균 나이(일): 기준이 오래될수록 완만한 추세(드리프트)가 편차로 새어 들어온다

    @property
    def z(self) -> float:
        return self.value / self.se if self.se > 0 else 0.0


@dataclass
class Episode:
    start: int              # 추정 시작일(1부터)
    end: int                # 마지막 경보일(1부터)
    alert_day: int          # 처음 경보가 *울린* 날(1부터)
    hits: dict[str, float] = field(default_factory=dict)   # 처음 경보 때 넘은 지표와 편차


# --------------------------------------------------------------------------- 관측 배열과 창
class _Series:
    def __init__(self, days: list[Day]) -> None:
        self.n = len(days)
        self.k = len(days[0].creatives) if days else 0
        self.imp = [d.impressions for d in days]
        self.clk = [d.clicks for d in days]
        self.conv = [d.conversions for d in days]
        self.spend = [d.spend for d in days]
        self.budget = [d.budget_krw for d in days]
        self.freq = [d.frequency for d in days]
        self.bounce = [d.bounce_rate for d in days]
        self.cimp = [[d.creatives[j]["impressions"] for d in days] for j in range(self.k)]
        self.cclk = [[d.creatives[j]["clicks"] for d in days] for j in range(self.k)]

    def sums(self, lo: int, hi: int) -> dict[str, float]:
        imp, clk = sum(self.imp[lo:hi + 1]), sum(self.clk[lo:hi + 1])
        conv, spend = sum(self.conv[lo:hi + 1]), sum(self.spend[lo:hi + 1])
        budget = sum(self.budget[lo:hi + 1])
        m = hi - lo + 1
        out = {
            "imp": imp, "clk": clk, "conv": conv, "spend": spend,
            "impressions": imp, "budget": budget / m, "cpm": spend / imp * 1000 if imp else 0.0,
            "util": spend / budget if budget else 0.0,
            "frequency": sum(self.freq[lo:hi + 1]) / m, "bounce": sum(self.bounce[lo:hi + 1]) / m,
        }
        for j in range(self.k):
            out[f"c{j}_impressions"] = sum(self.cimp[j][lo:hi + 1])
            out[f"c{j}_clk"] = sum(self.cclk[j][lo:hi + 1])
        return out


def ref_windows(flagged: Sequence[bool], i: int, w: int, kmax: int = K_REF) -> list[tuple[int, int]]:
    """창 끝 ``i``(0부터), 길이 ``w`` 의 기준 창 ``(lo, hi)`` 들. 안의 날이 모두 깨끗한 것만, 가까운 것부터 최대 ``kmax`` 개."""
    out: list[tuple[int, int]] = []
    if w <= 3:
        for wk in range(1, 9):                      # 같은 요일로 맞춘 wk 주 전 창
            e = i - 7 * wk
            lo = e - (w - 1)
            if lo < 0:
                break
            if not any(flagged[lo:e + 1]):
                out.append((lo, e))
                if len(out) >= kmax:
                    break
        return out
    e = i - w                                        # 7의 배수 길이: 이어진 깨끗한 창(요일 구성이 같다)
    while e - (w - 1) >= 0 and len(out) < kmax:
        lo = e - (w - 1)
        if not any(flagged[lo:e + 1]):
            out.append((lo, e))
            e = lo - 1
        else:
            e -= 1
    return out


class _Noise:
    """하루 단위 잡음의 크기. 깨끗한 날과 *7일 전 같은 요일*의 차이(로그 또는 절대)를 √2 로 나눈 값의 MAD 로 잰다 — 요일 패턴·완만한 추세가 상쇄된다.

    표본이 적을수록 보수적인 사전값(``PRIOR_DAY``)에 끌린다. 창 길이 ``w`` 의 합·평균에는 ``sd_day/√w`` 를 쓴다.
    """

    def __init__(self) -> None:
        self.daily: dict[str, list[float]] = {}

    def update(self, s: _Series, flagged: Sequence[bool], i: int) -> None:
        if i < 7 or flagged[i] or flagged[i - 7]:
            return
        util = [sp / bu if bu else 0.0 for sp, bu in ((s.spend[i], s.budget[i]), (s.spend[i - 7], s.budget[i - 7]))]
        pairs = {
            "impressions": (s.imp[i], s.imp[i - 7], True), "spend": (s.spend[i], s.spend[i - 7], True),
            "cpm": (s.spend[i] / s.imp[i] if s.imp[i] else 0.0, s.spend[i - 7] / s.imp[i - 7] if s.imp[i - 7] else 0.0, True),
            "util": (util[0], util[1], False), "frequency": (s.freq[i], s.freq[i - 7], False), "bounce": (s.bounce[i], s.bounce[i - 7], False),
        }
        for name, (a, b, log) in pairs.items():
            if log and (a <= 0 or b <= 0):
                continue
            self.daily.setdefault(name, []).append((math.log(a / b) if log else a - b) / math.sqrt(2))

    def sd_day(self, name: str) -> float:
        prior = PRIOR_DAY.get(name, PRIOR_DAY["impressions"])
        r = self.daily.get(name, [])
        n = len(r)
        if n < 3:
            return prior
        med = statistics.median(r)
        dev = sorted(abs(v - med) for v in r)
        # 사건이 아직 안 잡힌 날도 섞여 있으니(최대 ~30%) MAD(50% 분위) 대신 35% 분위로 척도를 잰다(정규분포의 35% 분위는 0.4538σ)
        s = dev[min(n - 1, int(0.35 * n))] / 0.4538
        s *= 1 + 1 / math.sqrt(2 * n)                      # 어림의 불확실성만큼 보수적으로
        return max(math.sqrt((N0 * prior ** 2 + n * s ** 2) / (N0 + n)), 0.3 * prior)

    def sd(self, name: str, w: int) -> float:
        return self.sd_day("impressions" if name.startswith("c") and name.endswith("_impressions") else name) / math.sqrt(w)


def _theil_sen(points: list[tuple[int, float]]) -> float:
    if len(points) < 10:
        return 0.0
    slopes = [(y2 - y1) / (t2 - t1) for a, (t1, y1) in enumerate(points) for t2, y2 in points[a + 1:] if t2 - t1 >= 3]
    return statistics.median(slopes) if slopes else 0.0


def _support_base(s: _Series, flagged: Sequence[bool], name: str, cur_center: float, refs: list[tuple[int, int]], i: int, w: int) -> float | None:
    """소진율·빈도·이탈률의 기준값: 기준 창 평균에 깨끗한 날들의 추세(Theil–Sen)를 얹어 현재 창 위치로 옮긴다."""
    if not refs:
        return None
    arr = {"util": [sp / bu if bu else 0.0 for sp, bu in zip(s.spend, s.budget, strict=True)], "frequency": s.freq, "bounce": s.bounce}[name]
    pts = [(t, arr[t]) for t in range(max(0, i - w - 35), i - w + 1) if not flagged[t]]
    slope = _theil_sen(pts) if name == "frequency" else 0.0   # 빈도만 완만하게 쌓인다(소진율·이탈률은 평평)
    vals = []
    for lo, hi in refs:
        m = sum(arr[lo:hi + 1]) / (hi - lo + 1)
        if name == "util":                               # 비율은 합으로 다시 구한다
            m = sum(s.spend[lo:hi + 1]) / sum(s.budget[lo:hi + 1])
        vals.append(m + slope * (cur_center - (lo + hi) / 2))
    return statistics.median(vals)


def window_devs(s: _Series, flagged: Sequence[bool], i: int, w: int, noise: _Noise) -> tuple[dict[str, Dev], int]:
    """창 ``[i-w+1, i]`` 의 지표별 편차와 표준오차, 기준 창 수. 기준 창이 없으면 ``({}, 0)``."""
    lo = i - w + 1
    if lo < 0:
        return {}, 0
    refs = ref_windows(flagged, i, w)
    if not refs:
        return {}, 0
    k = len(refs)
    inflate = math.sqrt(1 + 1 / k)
    age = i - sum((a + b) / 2 for a, b in refs) / k - (w - 1) / 2 + (w - 1) / 2
    cur = s.sums(lo, i)
    rs = [s.sums(a, b) for a, b in refs]
    out: dict[str, Dev] = {}
    # (1) 로그정규 잡음이 낀 지표: 상대 편차, 표준오차는 깨끗한 창의 편차에서
    names = ["impressions", "spend", "cpm", "budget", *[f"c{j}_impressions" for j in range(s.k)]]
    for name in names:
        base = statistics.median(r[name] for r in rs)
        if base:
            out[name] = Dev(cur[name] / base - 1, noise.sd(name, w) * inflate, k, age)

    # (2) 개수의 비율: 이항 z, 기준 창의 불확실성 포함
    def ratio(name: str, num: str, den: str) -> None:
        nr, dr = sum(r[num] for r in rs), sum(r[den] for r in rs)
        if cur[den] <= 0 or dr <= 0 or nr < 5:
            return
        p = nr / dr
        if p <= 0 or p >= 1:
            return
        exp = cur[den] * p
        if exp < 5:
            return
        rel = cur[num] / cur[den] / p - 1
        se = math.sqrt(DEFF * (1 - p) * (1 / exp + 1 / nr))
        out[name] = Dev(rel, se, k, age)

    ratio("ctr", "clk", "imp")
    ratio("cvr", "conv", "clk")
    for j in range(s.k):
        nr, dr = sum(r[f"c{j}_clk"] for r in rs), sum(r[f"c{j}_impressions"] for r in rs)
        if cur[f"c{j}_impressions"] > 0 and dr > 0 and nr >= 5:
            p = nr / dr
            exp = cur[f"c{j}_impressions"] * p
            if exp >= 5 and 0 < p < 1:
                out[f"c{j}_ctr"] = Dev(cur[f"c{j}_clk"] / cur[f"c{j}_impressions"] / p - 1, math.sqrt(DEFF * (1 - p) * (1 / exp + 1 / nr)), k, age)
    # (3) 지원 지표(절대 편차): 소진율·빈도·이탈률
    center = (lo + i) / 2
    for name in SUPPORT:
        sb = _support_base(s, flagged, name, center, refs, i, w)
        if sb is not None:
            out[name] = Dev(cur[name] - sb, noise.sd(name, w) * inflate, k, age)
    return out, k


def _triggers(devs: dict[str, Dev]) -> dict[str, Dev]:
    return {n: d for n, d in devs.items() if n not in SUPPORT and n != "budget_unused"}


def _hit(d: Dev) -> bool:
    return abs(d.z) >= Z_ALERT and abs(d.value) >= REL_MIN + DRIFT * d.age


# --------------------------------------------------------------------------- 탐지
@dataclass
class Detection:
    episodes: list[Episode]
    flagged: list[bool]
    alert_days: list[int]                 # 경보 상태였던 날(0부터)
    series: _Series
    noise: _Noise
    n: int


def detect(obs: Observation, upto: int | None = None) -> Detection:
    """``upto`` 일(1부터)까지의 관측만으로 경보를 만든다(인과적)."""
    n = upto if upto is not None else len(obs.days)
    n = min(n, len(obs.days))
    s = _Series(obs.days[:n])
    noise = _Noise()
    flagged = [False] * n
    last_hit: dict[tuple[str, int], tuple[int, int]] = {}
    alert = [False] * n
    first_hits: dict[int, dict[str, float]] = {}
    for i in range(n):
        devs_by_w: dict[int, tuple[dict[str, Dev], int]] = {}
        alert_w = 0
        hits_now: dict[str, float] = {}
        for w in WINDOWS:
            devs, k = window_devs(s, flagged, i, w, noise)
            devs_by_w[w] = (devs, k)
            for name, d in _triggers(devs).items():
                if not _hit(d):
                    continue
                sign = 1 if d.value > 0 else -1
                key = (name, w)
                big = abs(d.value) >= SINGLE_DAY and abs(d.z) >= Z_ALERT + 1
                persistent = last_hit.get(key) == (i - 1, sign) or big
                last_hit[key] = (i, sign)
                if not persistent:
                    continue
                if d.k < 2 and not (abs(d.value) >= SINGLE_DAY and abs(d.z) >= Z_ALERT + 1.5):
                    continue    # 기준 창이 하나뿐이면 그 창의 우연한 잡음이 그대로 편차가 된다 — 아주 큰 변화만 인정
                if w > 3:    # 긴 창의 메아리(사건이 끝난 뒤에도 창에 남은 효과)는 최근 3일이 창 평균 효과의 절반 이상일 때만 인정한다
                    d3 = devs_by_w[3][0].get(name)
                    if d3 is None or d3.value * d.value <= 0 or abs(d3.value) < max(REL_MIN / 2, 0.5 * abs(d.value)):
                        continue
                alert_w = w if alert_w == 0 else min(alert_w, w)
                hits_now[name] = d.value
        if alert_w:
            alert[i] = True
            for t in range(max(0, i - alert_w - 1), i + 1):    # 사건이 창보다 하루 먼저 시작했을 수 있어 하루 더 뺀다
                flagged[t] = True
            if i == 0 or not alert[i - 1]:
                first_hits[i] = hits_now
        noise.update(s, flagged, i)
    episodes = _episodes(s, flagged, alert, first_hits, noise)
    return Detection(episodes, flagged, [t for t in range(n) if alert[t]], s, noise, n)


def _episodes(s: _Series, flagged: list[bool], alert: list[bool], first_hits: dict[int, dict[str, float]], noise: _Noise) -> list[Episode]:
    out: list[Episode] = []
    n = len(alert)
    i = 0
    while i < n:
        if not alert[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and (alert[j + 1] or (j + 2 < n and alert[j + 2])):   # 하루 틈은 같은 구간으로 본다
            j += 1
        while not alert[j]:
            j -= 1
        hits = first_hits.get(i, {})
        out.append(Episode(_onset(s, flagged, i, hits, noise) + 1, j + 1, i + 1, hits))
        i = j + 1
    return out


def _onset(s: _Series, flagged: list[bool], i0: int, hits: dict[str, float], noise: _Noise) -> int:
    """첫 경보일 ``i0`` 직전에 효과가 시작된 날(0부터) 추정: 3일 창 편차가 최근 평탄값의 절반을 처음 넘은 창의 가운데."""
    if not hits:
        return i0
    name = max(hits, key=lambda k: abs(hits[k]))
    series: dict[int, float] = {}
    for e in range(max(2, i0 - 18), i0 + 1):
        devs, _ = window_devs(s, flagged, e, 3, noise)
        d = devs.get(name)
        if d is not None:
            series[e] = d.value
    recent = [series[e] for e in (i0 - 1, i0) if e in series]
    if not recent:
        return i0
    plateau = sum(recent) / len(recent)
    sign = 1 if plateau > 0 else -1
    for e in sorted(series):
        if series[e] * sign >= 0.5 * abs(plateau) and all(series.get(e + t, plateau) * sign >= 0.35 * abs(plateau) for t in (1, 2) if e + t <= i0):
            return max(0, e - 1)
    return max(0, i0 - 2)


# --------------------------------------------------------------------------- 진단
@dataclass
class Evidence:
    cause: CauseName
    score: float                                # 사후확률(0~1)
    detail: str
    magnitude: float = 0.0
    creative: int | None = None


@dataclass
class Diagnosis:
    episode: Episode
    candidates: list[Evidence]
    features: dict[str, float]
    z: dict[str, float]
    explained: bool                              # 서명이 데이터를 충분히 설명하는가
    second: Evidence | None = None               # 복합 사건이면 두 번째 원인
    delta_chi2: float = 0.0

    @property
    def top(self) -> CauseName | None:
        return self.candidates[0].cause if self.candidates and self.explained else None

    @property
    def causes(self) -> list[CauseName]:
        out = [self.top] if self.top else []
        if self.second and self.top:
            out.append(self.second.cause)
        return out


def _ols(rows: list[list[float]], y: list[float]) -> tuple[list[float], list[list[float]], float] | None:
    """작은 최소제곱(정규방정식을 가우스 소거로 푼다). (계수, (X'X)^-1, 잔차 제곱합) 또는 특이하면 None."""
    p = len(rows[0])
    a = [[sum(r[i] * r[j] for r in rows) for j in range(p)] + [1.0 if i == j else 0.0 for j in range(p)] for i in range(p)]
    for c in range(p):
        piv = max(range(c, p), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-12:
            return None
        a[c], a[piv] = a[piv], a[c]
        pv = a[c][c]
        a[c] = [v / pv for v in a[c]]
        for r in range(p):
            if r != c:
                f = a[r][c]
                a[r] = [v - f * w for v, w in zip(a[r], a[c], strict=True)]
    inv = [row[p:] for row in a]
    xty = [sum(r[i] * yy for r, yy in zip(rows, y, strict=True)) for i in range(p)]
    beta = [sum(inv[i][j] * xty[j] for j in range(p)) for i in range(p)]
    rss = sum((yy - sum(bj * rj for bj, rj in zip(beta, r, strict=True))) ** 2 for r, yy in zip(rows, y, strict=True))
    return beta, inv, rss


def _freq_step(freq: list[float], onset0: int, end0: int) -> Dev | None:
    """빈도 = 절편 + 추세 + (시작일 이후 단계) 로 맞춘 단계의 크기. 빈도는 사건이 없어도 천천히 오르므로(추세) 사건 *전*의 값과 그냥 비교하면 안 되고,
    사건 시작을 모르는 채 추세만 맞추면 사건이 추세에 먹힌다 — 둘을 한꺼번에 맞춰 시작일 후보 중 잔차가 가장 작은 것을 쓴다."""
    n = end0 + 1
    if n < 12:
        return None
    best: tuple[float, float, float] | None = None
    for st in range(max(8, onset0 - 8), min(end0 - 2, onset0 + 4) + 1):
        rows = [[1.0, float(t), 1.0 if t >= st else 0.0] for t in range(n)]
        fit = _ols(rows, freq[:n])
        if fit is None:
            continue
        beta, inv, rss = fit
        sigma2 = rss / max(1, n - 3)
        cand = (rss, beta[2], math.sqrt(max(sigma2 * inv[2][2], 0.0)))
        if best is None or cand[0] < best[0]:
            best = cand
    return Dev(best[1], best[2]) if best else None


def episode_devs(det: Detection, ep: Episode, last: int | None = None) -> dict[str, Dev]:
    """구간 후반(3일 창이 사건 안에 온전히 든 날들)의 평균 편차. 표준오차는 겹침을 감안해 √(창 수/3) 로 줄인다.

    기준 창은 경보 때 표시한 날에 더해 *추정 시작일 앞 2일부터 구간 끝까지*도 뺀다(경보가 늦었으면 사건 초반이 기준에 섞이기 때문)."""
    mask = list(det.flagged)
    last_end = (min(ep.end, last) if last else ep.end)
    for t in range(max(0, ep.start - 3), last_end):
        mask[t] = True
    last0 = last_end - 1
    first0 = min(last0, max(ep.start - 1 + 2, last0 - 6))
    acc: dict[str, list[Dev]] = {}
    for e in range(first0, last0 + 1):
        devs, _ = window_devs(det.series, mask, e, 3, det.noise)
        for name, d in devs.items():
            acc.setdefault(name, []).append(d)
    out: dict[str, Dev] = {}
    for name, ds in acc.items():
        n_eff = max(1.0, len(ds) / 3)
        out[name] = Dev(sum(d.value for d in ds) / len(ds), (sum(d.se for d in ds) / len(ds)) / math.sqrt(n_eff))
    fs = _freq_step(det.series.freq, ep.start - 1, last0)
    if fs is not None:
        out["frequency"] = fs
    return out


def _se(name: str, d: Dev) -> float:
    err = CREATIVE_ERR if name.startswith("c") and name[1:2].isdigit() else MODEL_ERR.get(name, 0.03)
    return math.sqrt(d.se ** 2 + err ** 2)


_REL = ("budget", "cpm", "ctr", "cvr")   # (1+a)(1+b)-1 로 합성하는 상대 지표. 소재별 지표도 상대.


def _combine(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    out = dict(a)
    for k, v in b.items():
        if k in out:
            out[k] = out[k] + v if k in SUPPORT else (1 + out[k]) * (1 + v) - 1
        else:
            out[k] = v
    return out


@dataclass
class _Hyp:
    cause: CauseName
    creative: int | None


def _signature(h: _Hyp, m: float, ctx: dict[str, Any]) -> dict[str, float]:
    c = h.cause
    if c == "budget_cut":
        return {"budget": -m}
    if c == "bid_drop":
        return {"util": -m * ctx["util"]}
    if c == "seasonal_cpm_spike":
        return {"cpm": m}
    if c == "audience_saturation":
        return {"cpm": m, "frequency": 0.9 * m}
    if c == "tracking_outage":
        return {"cvr": -m}
    if c == "landing_degradation":
        return {"cvr": -m, "bounce": 0.35 * m}
    j = h.creative if h.creative is not None else 0
    share = ctx["share"][j]
    if c == "creative_fatigue":
        return {f"c{j}_ctr": -m, "ctr": -m * share, "frequency": 0.5 * m}
    # ad_rejected: 대상 소재 노출 −100%, 나머지는 그 몫을 나눠 갖는다
    out = {f"c{j}_impressions": -1.0}
    for o in range(ctx["k"]):
        if o != j:
            out[f"c{o}_impressions"] = share / (1 - share) if share < 1 else 0.0
    others = [ctx["ctr"][o] for o in range(ctx["k"]) if o != j]
    if others:
        mean_all = sum(ctx["ctr"]) / ctx["k"]
        out["ctr"] = (sum(others) / len(others)) / mean_all - 1 if mean_all else 0.0
    return out


def _grid(h: _Hyp) -> list[float]:
    return [1.0] if h.cause == "ad_rejected" else [round(0.05 * q, 3) for q in range(1, 21)]


def _chi2(x: dict[str, Dev], pred: dict[str, float]) -> float:
    return sum(((d.value - pred.get(n, 0.0)) / _se(n, d)) ** 2 for n, d in x.items())


def _hist_bonus(events: list[Event], ep: Episode, cause: CauseName) -> float:
    kind = {"budget_cut": "budget_change", "bid_drop": "bid_change", "ad_rejected": "ad_rejected", "tracking_outage": "tracking_change",
            "landing_degradation": "landing_change"}.get(cause)
    if kind and any(e.type == kind and ep.start - 5 <= e.day <= ep.alert_day for e in events):
        return math.log(3.0)
    return 0.0


def diagnose(obs: Observation, det: Detection, ep: Episode, last: int | None = None) -> Diagnosis:
    x = episode_devs(det, ep, last)
    f = {n: d.value for n, d in x.items()}
    zs = {n: d.z for n, d in x.items()}
    k = det.series.k
    if not x:
        return Diagnosis(ep, [], f, zs, False)
    # 서명에 쓰는 맥락: 기준 구간(경보 전)의 소재별 클릭 몫·CTR, 소진율
    lo = max(0, ep.alert_day - 1 - 21)
    hi = max(lo, ep.start - 2)
    base = det.series.sums(lo, hi)
    clk_tot = sum(base[f"c{j}_clk"] for j in range(k)) or 1.0
    ctx = {
        "k": k, "util": base["util"] or BASE_UTIL,
        "share": [base[f"c{j}_clk"] / clk_tot for j in range(k)],
        "ctr": [base[f"c{j}_clk"] / base[f"c{j}_impressions"] if base[f"c{j}_impressions"] else 0.0 for j in range(k)],
    }
    chi0 = _chi2(x, {})
    hyps = [_Hyp(c, None) for c in ("budget_cut", "bid_drop", "audience_saturation", "seasonal_cpm_spike", "tracking_outage", "landing_degradation")]
    for j in range(k):
        hyps += [_Hyp("creative_fatigue", j), _Hyp("ad_rejected", j)]
    best: dict[int, tuple[float, float]] = {}      # 가설 번호 → (최소 χ², m)
    for idx, h in enumerate(hyps):
        best[idx] = min((_chi2(x, _signature(h, m, ctx)), m) for m in _grid(h))
    T = 2.0
    logw = {q: -best[q][0] / (2 * T) + _hist_bonus(obs.events, ep, hyps[q].cause) for q in range(len(hyps))}
    ranked = sorted(range(len(hyps)), key=lambda q: -logw[q])
    mx = logw[ranked[0]]
    mass: dict[CauseName, float] = {}
    rep: dict[CauseName, int] = {}
    for q in ranked:
        c = hyps[q].cause
        mass[c] = mass.get(c, 0.0) + math.exp(logw[q] - mx)
        rep.setdefault(c, q)
    tot = sum(mass.values()) or 1.0
    cands: list[Evidence] = []
    for cause in sorted(mass, key=lambda c: -mass[c]):
        q = rep[cause]
        cands.append(Evidence(cause, round(mass[cause] / tot, 3), _explain(hyps[q], best[q][1], f), best[q][1], hyps[q].creative))
    top_q = ranked[0]
    delta = chi0 - best[top_q][0]
    second: Evidence | None = None
    # 복합 사건: 서로 다른 두 원인을 함께 맞춘 χ² 가 단일 원인보다 충분히 작은가
    best_pair: tuple[float, int, int, float, float] | None = None
    coarse = [round(0.1 * q, 2) for q in range(1, 11)]
    top_idx = [q for q in ranked[:8]]
    for a in top_idx:
        for b in top_idx:
            if a >= b or hyps[a].cause == hyps[b].cause:
                continue
            for ma in ([1.0] if hyps[a].cause == "ad_rejected" else coarse):
                sa = _signature(hyps[a], ma, ctx)
                for mb in ([1.0] if hyps[b].cause == "ad_rejected" else coarse):
                    c2 = _chi2(x, _combine(sa, _signature(hyps[b], mb, ctx)))
                    if best_pair is None or c2 < best_pair[0]:
                        best_pair = (c2, a, b, ma, mb)
    if best_pair and best[top_q][0] - best_pair[0] >= 12.0 and delta >= 12.0:
        _, a, b, ma, mb = best_pair
        ha, hb = hyps[a], hyps[b]
        sa_only = chi0 - _chi2(x, _signature(ha, ma, ctx))
        sb_only = chi0 - _chi2(x, _signature(hb, mb, ctx))
        first, other = (ha, hb) if sa_only >= sb_only else (hb, ha)
        m_first, m_other = (ma, mb) if sa_only >= sb_only else (mb, ma)
        cands = [Evidence(first.cause, 0.6, _explain(first, m_first, f), m_first, first.creative)]
        second = Evidence(other.cause, 0.4, _explain(other, m_other, f), m_other, other.creative)
        delta = chi0 - best_pair[0]
    explained = delta >= 12.0
    return Diagnosis(ep, cands, {k2: round(v, 4) for k2, v in f.items()}, {k2: round(v, 2) for k2, v in zs.items()}, explained, second, round(delta, 1))


def _explain(h: _Hyp, m: float, f: dict[str, float]) -> str:
    g = f.get
    c = h.cause
    if c == "budget_cut":
        return f"일 예산 {g('budget', 0.0):+.0%}, 소진율은 그대로"
    if c == "bid_drop":
        return f"소진율 {g('util', 0.0):+.2f}p, 예산·CPM 은 그대로"
    if c == "audience_saturation":
        return f"CPM {g('cpm', 0.0):+.0%}, 빈도 {g('frequency', 0.0):+.2f}"
    if c == "seasonal_cpm_spike":
        return f"CPM {g('cpm', 0.0):+.0%}, 빈도는 그대로"
    if c == "tracking_outage":
        return f"전환율 {g('cvr', 0.0):+.0%}, 이탈률 변화 {g('bounce', 0.0):+.2f}(없음)"
    if c == "landing_degradation":
        return f"전환율 {g('cvr', 0.0):+.0%}, 이탈률 {g('bounce', 0.0):+.2f}"
    j = h.creative if h.creative is not None else 0
    if c == "creative_fatigue":
        return f"소재 {j} CTR {g(f'c{j}_ctr', 0.0):+.0%}, 노출·전환율은 그대로"
    return f"소재 {j} 노출 {g(f'c{j}_impressions', 0.0):+.0%}"


# --------------------------------------------------------------------------- 액션
_ALL_ACTIONS = ("raise_budget", "adjust_bid", "rotate_creative", "expand_audience", "fix_tracking", "fix_landing", "fix_rejected_ad", "wait_and_monitor")
U_OK, U_HARM = 1.0, -3.0


def _utility(action: str, causes: Sequence[CauseName]) -> float:
    if not causes:
        return 0.15 if action == "wait_and_monitor" else (U_HARM if action != "no_action" else 0.0)
    ok = any(action in ACCEPTABLE[c] for c in causes)
    if ok:
        return U_OK
    if any(action in HARMFUL[c] for c in causes):
        return U_HARM
    return 0.05 if action == "wait_and_monitor" else 0.0


def recommend(obs: Observation, dx: Diagnosis) -> list[dict[str, Any]]:
    """원인 사후확률에 대한 기대효용이 큰 액션(최대 2개). 숫자는 관측에서 계산한 값이다. 해로울 수 있는 액션은 ``avoid`` 로 따로 돌려준다."""
    f = dx.features
    if not dx.explained or not dx.candidates:
        return [{"action": "wait_and_monitor", "cause": None, "text": "뚜렷한 원인 서명이 없습니다. 변경하지 말고 며칠 더 관찰하세요.", "avoid": list(_ALL_ACTIONS[:-1]), "eu": 0.15}]
    hyp: list[tuple[float, list[CauseName]]]
    if dx.second:
        hyp = [(1.0, [dx.candidates[0].cause, dx.second.cause])]
    else:
        hyp = [(c.score, [c.cause]) for c in dx.candidates]
    eu = {a: sum(p * _utility(a, cs) for p, cs in hyp) for a in _ALL_ACTIONS}
    chosen = [a for a, _ in sorted(eu.items(), key=lambda kv: -kv[1]) if eu[a] >= 0.35][:2]
    if not chosen:
        chosen = ["wait_and_monitor"]
    text = {
        "raise_budget": f"일 예산을 이전 수준으로 되돌릴지 검토하세요(예산 {f.get('budget', 0):+.0%}).",
        "adjust_bid": f"입찰(목표 CPA)을 이전 수준으로 복구하세요(소진율 {f.get('util', 0):+.2f}p).",
        "rotate_creative": "피로한 소재를 새 소재로 교체하세요.",
        "expand_audience": f"타깃을 넓혀 도달을 늘리세요(CPM {f.get('cpm', 0):+.0%}, 빈도 {f.get('frequency', 0):+.2f}).",
        "fix_tracking": f"전환 태그·픽셀을 점검하세요(클릭·이탈률은 그대로인데 전환율 {f.get('cvr', 0):+.0%}).",
        "fix_landing": f"랜딩 페이지를 점검하세요(전환율 {f.get('cvr', 0):+.0%}, 이탈률 {f.get('bounce', 0):+.2f}).",
        "fix_rejected_ad": "반려된 소재의 사유를 확인해 수정하고 다시 심사받으세요.",
        "wait_and_monitor": f"외부 요인으로 보입니다(CPM {f.get('cpm', 0):+.0%}). 변경하지 말고 지켜보세요.",
    }
    plausible = {c for p, cs in hyp for c in cs if p >= 0.2}
    avoid = sorted({h for c in plausible for h in HARMFUL[c]} - set(chosen))
    out = []
    for a in chosen:
        causes = [c for _, cs in hyp for c in cs if a in ACCEPTABLE[c]] or [dx.candidates[0].cause]
        out.append({"action": a, "cause": causes[0], "text": text[a], "avoid": avoid, "eu": round(eu[a], 2)})
    return out
