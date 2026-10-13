# 심사 데모: 실제 실행과 저장 결과를 구분하기

## 진입

README의 설치 명령 후 `ADGATE_DEV=1 ADGATE_DB=var/demo.sqlite3 adgate serve --port 8000`.
주소: `http://127.0.0.1:8000`. 공개 호스팅은 이번 작업 범위에서 실행하지 않았다.

[실제 로컬 실행 영상](media/audit/review-walkthrough.mp4)

| 장면 | 조작·확인 | 실제 캡처 |
|---|---|---|
| 승인 | 승인 사례 → 심사하기. 위반 미발견 | [승인](media/audit/02-approve.png) |
| 반려 | 반려 → 수정 → 심사하기. 필수 고지와 사유 | [반려](media/audit/03-reject.png) |
| 수정 → 재심사 | 수정안 보기 → 차이 확인 → 폼에 채우기 → 수정안 재심사. 원본 심사 링크 | [수정안](media/audit/04-rewrite.png) |
| 원문 근거 | 띄어쓰기로 우회한 수익 보장 예시. 표시된 원문과 규칙 ID 대조 | [근거](media/audit/05-grounding.png) |
| 사람 검토 | 사람 검토 사례 제출 → bob 역할 → 대기열 → 사유 작성 → 결정 저장 | [사람 결정](media/audit/06-human-decision.png) |
| 기록 복원 | 새로고침, 저장 기록 열기. 새 심사 호출 없이 저장된 결과 표시 | [모바일](media/audit/07-mobile.png) |

영상은 현재 콘솔과 SQLite/HTTP 서버를 실제로 조작한 녹화다. 편집으로 판정 결과를 합성하지 않았다. 영상 속 결과는 결정론 규칙의 실시간 실행이며 LLM 실호출이 아니다. 가상 심사자의 결정도 데모 입력이다.

`저장된 결과 보기`는 DB에서 과거 결과를 읽는 것, `저장된 LLM 응답 재생`은 과거 모델 출력을 현재 파이프라인에 다시 넣는 평가다. 서로 다르다. 기본 서버는 `Settings.from_env()`에 Judge가 설정되지 않으므로 LLM 미호출을 화면에 명시한다. 다른 Judge는 Python의 `Settings(judge=...)`로 주입할 수 있지만 웹에서 임의 모델을 활성화하거나 키를 입력하는 기능은 없다.

## 권한과 실패 확인

- carol은 alice의 저장 기록·수정안을 API에서 읽을 수 없다. bob은 검토용으로 전체 목록을 볼 수 있지만 자기 제출 건을 결정할 수 없다.
- 결정 요청을 반복하면 409. 동일 멱등키+동일 본문의 제출은 저장 결과를 돌려주고, 같은 키의 다른 본문·원본 연결은 409.
- 새로고침은 결과와 생성된 수정안을 읽어 복원한다. 작성 중 미제출 초안·custom 토큰은 저장하지 않는다.
- 텍스트는 HTML로 실행하지 않고 원문 글자로 표시한다. 역할 전환·뒤늦은 HTTP 응답으로 이전 소재가 다시 나타나지 않게 한다.
- 후보의 수치/문장 보존 검사에 실패하면 부분 수정으로 표시한다. 단어·수치 검사는 의미 보존 증명이 아니므로 사람이 확인해야 한다.

## 검증·녹화 재현

```bash
pip install playwright==1.56.0
playwright install chromium
ADGATE_E2E=1 pytest tests/e2e/test_console.py -q
pytest tests/test_restart_audit.py -q
python scripts/capture_review_demo.py
ffmpeg -y -i docs/media/audit/review-walkthrough.webm -c:v libx264 -crf 31 -pix_fmt yuv420p -movflags +faststart -an docs/media/audit/review-walkthrough.mp4
```

녹화 스크립트는 로컬 8765 포트에서 가상 토큰 서버를 자식 프로세스로 띄우고 종료한다. 동작 검증은 별도 테스트가 담당한다. 한국어 폰트가 없는 실행 환경에서는 저장소의 OFL 폰트 또는 시스템 한국어 폰트를 설치해야 화면 글자가 보인다. 캡처 메타데이터: [capture.json](media/audit/capture.json).
