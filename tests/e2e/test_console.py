"""브라우저 e2e. 실제 Chromium 으로 콘솔을 조작한다. 기본은 건너뛰고 ADGATE_E2E=1 일 때만 돈다.

로컬에서 번들 크로미움을 쓰려면 PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium-1194/chrome-linux/chrome
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("ADGATE_E2E") != "1", reason="ADGATE_E2E=1 일 때만 실행")
playwright_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = free_port()
    env = {**os.environ, "ADGATE_DEV": "1", "ADGATE_DB": str(tmp_path_factory.mktemp("db") / "e2e.sqlite3")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "adgate.api:create_app", "--factory", "--port", str(port), "--log-level", "warning"],
        env=env, cwd=ROOT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{url}/healthz", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        else:
            raise RuntimeError("서버가 뜨지 않았습니다")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch(args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):  # type: ignore[no-untyped-def]
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    pg.dialogs = []
    pg.problems = []
    pg.on("dialog", lambda d: (pg.dialogs.append(d.message), d.dismiss()))
    pg.on("pageerror", lambda e: pg.problems.append(str(e)))
    pg.on("console", lambda m: pg.problems.append(m.text) if m.type == "error" and "403" not in m.text else None)
    pg.goto(base_url)
    pg.wait_for_selector("#me:not(:empty)")
    yield pg
    pg.close()


def submit_example(pg, name: str) -> None:  # type: ignore[no-untyped-def]
    pg.select_option("#example", name)
    pg.click("#submit")
    pg.wait_for_selector("#result:not([hidden])")


def test_rejection_highlights_the_exact_original_text(page):  # type: ignore[no-untyped-def]
    submit_example(page, "반려: 띄어쓰기로 우회한 수익 보장")
    assert page.inner_text("#badge") == "반려"
    assert page.eval_on_selector_all("mark", "els => els.map(e => e.textContent)") == ["수 익 보 장"]
    assert "AD-GUA-01" in page.inner_text("#reasons")


def test_same_words_in_a_compliant_context_are_approved(page):  # type: ignore[no-untyped-def]
    submit_example(page, "승인: 같은 단어, 부정문")
    assert page.inner_text("#badge") == "승인"
    assert page.eval_on_selector_all("mark", "els => els.length") == 0


def test_rewrite_is_verified_by_a_second_review(page):  # type: ignore[no-untyped-def]
    submit_example(page, "반려: 금융 필수 고지 누락")
    page.click("#rewrite")
    page.wait_for_selector("#rewriteBox:not([hidden])")
    text = page.inner_text("#rewriteBox")
    assert "검증됨" in text and "재심사 결과 APPROVE" in text


def test_reviewer_queue_resume_and_separation_of_duties(page):  # type: ignore[no-untyped-def]
    submit_example(page, "사람 검토: 근거 없는 긴급성")
    assert page.inner_text("#badge").startswith("사람 검토")
    assert not page.is_visible("#queueBox") and not page.is_visible("#reviewBox")  # 광고주에게는 보이지 않는다
    page.select_option("#role", "dev-reviewer")
    page.wait_for_selector("#me:has-text('bob')")
    page.wait_for_selector("#queue li button")
    page.click("#queue li button")
    page.wait_for_selector("#reviewBox:not([hidden])")
    page.fill("#reason", "랜딩에서 이벤트 기간을 확인했습니다")
    page.click("#resume")
    page.wait_for_selector("#badge:has-text('사람 결정')")
    assert page.inner_text("#badge") == "승인 (사람 결정)"
    # 심사자가 직접 제출한 건은 직무 분리로 막힌다
    submit_example(page, "사람 검토: 근거 없는 긴급성")
    page.fill("#reason", "자기 건 승인 시도입니다")
    page.click("#resume")
    page.wait_for_selector("#error:not([hidden])")
    assert "직무 분리" in page.inner_text("#error")


def test_ad_copy_with_html_is_rendered_as_inert_text(page):  # type: ignore[no-untyped-def]
    page.fill("#headline", '<img src=x onerror="alert(1)"><script>alert(2)</script> 무조건 승인')
    page.fill("#body", "<b>굵게</b>")
    page.click("#submit")
    page.wait_for_selector("#result:not([hidden])")
    assert page.dialogs == []
    assert page.eval_on_selector_all("#fields img, #fields script, #fields b", "els => els.length") == 0
    assert "<img src=x" in page.inner_text("#fields")


def test_highlight_offsets_survive_emoji(page):  # type: ignore[no-untyped-def]
    page.fill("#headline", "😊✨ 무조건 승인 🌿")
    page.click("#submit")
    page.wait_for_selector("#result:not([hidden])")
    assert page.eval_on_selector_all("mark", "els => els.map(e => e.textContent)") == ["무조건 승인"]


def test_no_csp_violations_or_page_errors_and_no_mobile_overflow(page):  # type: ignore[no-untyped-def]
    submit_example(page, "반려: 질병 치료 표방")
    page.set_viewport_size({"width": 390, "height": 800})
    assert page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth") is False
    assert page.problems == []


def test_submit_works_when_crypto_random_uuid_is_missing(browser, base_url):  # type: ignore[no-untyped-def]
    """리뷰 L3: crypto.randomUUID 는 보안 컨텍스트에서만 있다. 비보안 주소(http://다른-IP)로 열면 제출이 깨졌다."""
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.add_init_script("Object.defineProperty(crypto, 'randomUUID', { value: undefined, configurable: true });")
    pg.goto(base_url)
    pg.wait_for_selector("#me:not(:empty)")
    submit_example(pg, "반려: 띄어쓰기로 우회한 수익 보장")
    assert pg.inner_text("#badge") == "반려" and problems == []
    pg.close()
