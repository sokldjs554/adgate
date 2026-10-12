"""사람 실험 책자(catalog/study.py 의 HTML)를 실제 Chromium 으로 끝까지 조작한다. 기본은 건너뛰고 ADGATE_E2E=1 일 때만 돈다.

확인: 오프라인으로 동작(네트워크 요청 0), 제안 패널이 assisted 시도에만 보임, 응답 파일의 형식·개수, 소재 글에 든 ``</script>`` 가 코드로 실행되지 않음.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("ADGATE_E2E") != "1", reason="ADGATE_E2E=1 일 때만 실행")
playwright_api = pytest.importorskip("playwright.sync_api")

from adgate.catalog import study  # noqa: E402
from adgate.eval import dataset  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def kit(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    pool = dataset.load(ROOT / "data/redteam.jsonl") + dataset.load(ROOT / "data/gray.jsonl")
    items = study.select_items(pool, per_stratum=4, seed=2)
    trials = study.design(items, 4, seed=2)
    practice = study.practice_items(dataset.load(ROOT / "data/dev.jsonl"))
    by = {i.id: i for i in items}
    path = tmp_path_factory.mktemp("kit") / "R01.html"
    path.write_text(study.booklet("R01", trials, by, practice, "e2e"), encoding="utf-8")
    return {"path": path, "trials": [t for t in trials if t.reviewer == "R01"], "practice": practice, "items": by}


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with playwright_api.sync_playwright() as p:
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch(args=["--no-sandbox"])
        yield b
        b.close()


def run_through(browser, path: Path, decide) -> tuple[list[str], list[bool], dict]:  # type: ignore[no-untyped-def]
    page = browser.new_page()
    requests: list[str] = []
    problems: list[str] = []
    page.on("request", lambda r: requests.append(r.url) if not r.url.startswith(("file:", "blob:", "data:")) else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(path.as_uri())
    page.click("#go")
    hints: list[bool] = []
    i = 0
    while True:
        if page.query_selector("a[download]"):
            break
        hints.append(page.query_selector(".hint") is not None)
        page.click(f"button.d >> nth={decide(i)}")
        i += 1
    href = page.get_attribute("a[download]", "href")
    payload = json.loads(page.evaluate("href => fetch(href).then(r => r.text())", href))
    page.close()
    assert not requests and not problems, (requests, problems)
    return [], hints, payload


def test_booklet_runs_offline_end_to_end_and_exports_the_responses(browser, kit):  # type: ignore[no-untyped-def]
    _, hints, payload = run_through(browser, kit["path"], lambda i: i % 3)
    trials = kit["trials"]
    practice = kit["practice"]
    assert len(hints) == len(practice) + len(trials)
    # 연습(앞 2건)은 기록되지 않고, 제안 패널은 assisted 시도에만 보인다
    assert hints[len(practice):] == [t.condition == "assisted" for t in trials]
    rows = payload["responses"]
    assert [r["item"] for r in rows] == [t.item_id for t in trials] and [r["condition"] for r in rows] == [t.condition for t in trials]
    assert all(isinstance(r["ms"], int) and r["ms"] >= 0 for r in rows) and payload["reviewer"] == "R01" and payload["design"] == "e2e"
    assert [r["decision"] for r in rows] == [("APPROVE", "REVIEW", "REJECT")[(len(practice) + i) % 3] for i in range(len(rows))]
    assert study.parse_responses(payload)                                           # 분석 코드가 그대로 읽는다


def test_creative_text_cannot_inject_script_into_the_booklet(browser, tmp_path):  # type: ignore[no-untyped-def]
    evil = '</script><img src=x onerror="window.__pwned=1">'
    base = study.StudyItem("X-1", "clear_correct", {"advertiser": {"name": evil, "category": "general", "verified": True}, "headline": evil, "body": "<b>굵게</b>", "cta": "", "disclosure": None}, "APPROVE", "APPROVE", (), ())
    trials = [study.Trial("R01", 1, "X-1", "assisted", 1)]
    path = tmp_path / "evil.html"
    path.write_text(study.booklet("R01", trials, {"X-1": base}, [], "e2e"), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.click("#go")
    assert page.evaluate("window.__pwned") is None
    text = page.inner_text("main")
    assert "</script><img" in text and "<b>굵게</b>" in text                      # 글자 그대로 보인다
    assert page.query_selector("img") is None                                   # 소재의 <img> 는 요소가 되지 않는다
    assert "굵게" not in page.eval_on_selector_all("main b", "els => els.map(e => e.textContent)")   # 소재의 <b> 도 요소가 되지 않는다(라벨용 <b> 는 책자 자신의 것)
    page.close()
