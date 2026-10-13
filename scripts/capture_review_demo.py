"""Start a disposable local server and record the real console on port 8765."""
from pathlib import Path
import json
import time
import subprocess
import os
import sys
import urllib.request
import hashlib
import tempfile
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
out = root/'docs/media/audit'
out.mkdir(parents=True, exist_ok=True)
database_dir = tempfile.TemporaryDirectory(prefix='adgate-capture-')
proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'adgate.api:create_app', '--factory', '--port', '8765', '--log-level', 'warning'], cwd=root, env={**os.environ, 'ADGATE_DEV':'1', 'ADGATE_DB':str(Path(database_dir.name)/'demo.sqlite3'), 'ADGATE_RATE_PER_MIN':'1000'})
import atexit
atexit.register(lambda: (proc.terminate(), proc.wait(timeout=10)))
for _ in range(100):
    try:
        urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=1)
        break
    except Exception:
        time.sleep(.1)
with sync_playwright() as p:
    browser = p.chromium.launch(args=['--no-sandbox'])
    context = browser.new_context(viewport={'width':1280,'height':1000},
                                  record_video_dir=str(out/'raw'), record_video_size={'width':1280,'height':1000})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto('http://127.0.0.1:8765')
    page.wait_for_selector('#me:has-text("alice")')
    page.screenshot(path=str(out/'01-start.png'), full_page=True)
    time.sleep(2)
    def submit(name):
        page.select_option('#example', name)
        with page.expect_response(lambda r: r.url.endswith('/v1/reviews') and r.request.method == 'POST'):
            page.click('#submit')
        page.wait_for_selector('#result:not([hidden])')
        time.sleep(1)
    submit('승인: 일반 앱')
    assert page.inner_text('#badge') == '승인'
    page.screenshot(path=str(out/'02-approve.png'), full_page=True)
    time.sleep(3)
    submit('반려: 금융 필수 고지 누락')
    assert page.inner_text('#badge') == '반려'
    page.screenshot(path=str(out/'03-reject.png'), full_page=True)
    time.sleep(3)
    page.click('#rewrite')
    page.wait_for_selector('#rewriteBox:not([hidden])')
    page.locator('#rewriteBox').scroll_into_view_if_needed()
    page.screenshot(path=str(out/'04-rewrite.png'), full_page=True)
    time.sleep(4)
    page.click('#useRewrite')
    page.click('#submit')
    page.wait_for_selector('#badge:has-text("승인")')
    page.locator('#result').scroll_into_view_if_needed()
    time.sleep(3)
    submit('반려: 띄어쓰기로 우회한 수익 보장')
    assert page.locator('mark').first.inner_text() == '수 익 보 장'
    page.locator('#result').scroll_into_view_if_needed()
    page.screenshot(path=str(out/'05-grounding.png'), full_page=True)
    time.sleep(4)
    submit('사람 검토: 근거 없는 긴급성')
    page.select_option('#role','dev-reviewer')
    page.wait_for_selector('#me:has-text("bob")')
    page.locator('#queue li button').first.click()
    page.wait_for_selector('#reviewBox:not([hidden])')
    page.locator('#reviewBox').scroll_into_view_if_needed()
    time.sleep(2)
    page.check('input[name="verdict"][value="REJECT"]')
    page.fill('#reason','합성 데모: 기간 근거가 없어 반려로 결정했습니다.')
    page.click('#resume')
    page.wait_for_selector('#badge:has-text("사람 결정")')
    assert '반려' in page.inner_text('#badge')
    page.locator('#result').scroll_into_view_if_needed()
    page.screenshot(path=str(out/'06-human-decision.png'), full_page=True)
    time.sleep(3)
    page.reload()
    page.wait_for_selector('#badge:has-text("사람 결정")')
    assert '저장된 결과' in page.inner_text('#execution')
    time.sleep(3)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    page.screenshot(path=str(out/'07-mobile.png'), full_page=True)
    assert not errors, errors
    video = page.video
    page.close()
    context.close()
    video.save_as(str(out/'review-walkthrough.webm'))
    browser.close()
    (out/'capture.json').write_text(json.dumps({'captured_at':datetime.now(timezone.utc).isoformat(),'source':'http://127.0.0.1:8765','mode':'deterministic live local server','browser':'Chromium via Playwright 1.56.0','app_js_sha256':hashlib.sha256((root/'src/adgate/web/app.js').read_bytes()).hexdigest(),'page_errors':errors,'mobile_overflow':False,'scenes':['approve','reject','rewrite','resubmit','original quote','queue','human decision','reload']},ensure_ascii=False,indent=2))
