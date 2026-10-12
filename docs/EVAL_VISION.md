# 이미지 읽기 평가 사전등록

이 문서의 **1–6절은 측정 전에 쓴 것**이고 해시를 `docs/PREREGISTRATIONS.json` 이 고정한다(`adgate prereg`). 결과와 '빗나감'은 8절 이후에 덧붙인다. 예상이 틀려도 1–6절은 고치지 않는다.
구현은 `src/adgate/vision/`(사양 `spec.py`, 렌더러 `render.py`, 읽기 `read.py`, 채점 `evaluate.py`, 묶음 `suite.py`), 데이터는 [data/vision/](../data/vision).

## 1. 무엇을 알고 싶은가

지금까지 심사 평가는 이미지 속 글자를 **이미 텍스트로 받은 값**(`image_text`)이라고 가정했다. 실제 광고의 위반은 종종 이미지 속 글자에 있다(제목·본문은 깨끗한데 배너에 ‘수익 보장’).
이 평가는 *이미지 자체*를 모델(VLM)에게 읽히고, 읽은 결과가 심사 판정을 어떻게 바꾸는지 잰다.

- **Q1.** 모델은 광고 이미지 속 글자를 얼마나 정확히 옮기는가? 특히 작은 글씨(≤ 12px)·흐린 글씨(투명도 ≤ 0.4)·회전한 글씨를.
- **Q2.** 지어내는가? 정답에 없는 글자를 내놓는 비율은?
- **Q3.** 시각 단서(전·후 비교, 카운트다운, 가짜 시스템 창, 수상 배지, 작은 글씨, 흐린 글씨, 별점)를 알아보는가?
- **Q4.** 이미지 속 글자를 (a) 안 주면 (b) 모델이 읽은 것을 주면 (c) 정답 글자를 주면 — 같은 결정론 심사의 정확도와 위험 승인율이 어떻게 달라지는가? 읽기가 병목인가, 심사 엔진이 병목인가?
- **Q5.** 읽기에 실패하면 승인하지 않는가(fail-closed)?
- **Q6.** 더 작은 모델(haiku)·더 강한 모델(opus)은 sonnet 과 얼마나 다른가?

## 2. 데이터

[data/vision/vision.jsonl](../data/vision/vision.jsonl) — 이미지 광고 소재 60건(조각 A–F × 10), 독립 작성자 6명이 *정책 문서와 이미지 사양 설명*(과 형식 검사기)만 보고 썼다(같은 모델 계열의 별도 컨텍스트 LLM 이지 사람이 아니다). 이미지는 사양(JSON)에서 그려 PNG 로 커밋한다(`data/vision/images/`). 정답(이미지 속 글자 덩어리, 시각 단서)은 사양에서 **기계적으로** 나오고 심사 정답은 작성자가 정책을 읽은 결과다. 전체가 **동결된 평가셋**이다(`MANIFEST.sha256`, 파일 61개).

구성(코드로 센 값): 정답 판정 REJECT 25 · REVIEW 16 · APPROVE 19. 구성 태그 — **이미지에만 위반(image_only) 24 · 정상 유사(benign_image) 18 · 시각 단서(visual_cue) 12 · 텍스트와 이미지 둘 다(both) 6**.
업종 이커머스 10 · 앱 9 · 일반 9 · 교육 7 · 투자 6 · 대출 6 · 건강기능식품 6 · 화장품 6 · 도박 1. 위반 규칙 FRD 9 · ABS 7 · URG 7 · GUA 6 · CMP 6 · HLT 6 · ENDO 5 · BRD 3 · FIN 1 · LND 1.
이미지 글자 덩어리 428개 중 **읽기 어려운 것(작은 글씨 ≤ 12px · 흐린 글씨 ≤ 0.4) 65개**(어려운 글자가 하나라도 있는 소재 51건), 회전한 글자 항목 31개. 정답 시각 단서 — 작은 글씨 49 · 흐린 글씨 13 · 가짜 시스템 창 7 · 수상 배지 6 · 카운트다운 3 · 별점 3 · 전·후 비교 2(단서가 하나라도 있는 소재 58건). 캔버스 크기 38종.

이미지는 **평평한 도형과 글자로 그린 합성 이미지**이고 사진·손글씨·실제 로고가 없다(8절).
설계자는 렌더러를 점검하고(작성자 E 가 보고한 회전 글자 문제로 렌더러를 vision-v2 로 고쳤다) 형식 검사를 돌리느라 사양과 그려진 이미지를 볼 수 있었다. 읽기 모델의 출력과 심사 엔진의 이 셋 위 결과는 측정 전에 본 적이 없다. 작성자가 알려 준 정책·사양의 공백은 [data/vision/README.md](../data/vision/README.md) 에 있다.
읽기 프롬프트(`vision.read` v1)는 이 셋을 보기 전에 쓴 것이고 이 평가 때문에 바꾸지 않는다.

## 3. 팔

| 팔 | 내용 |
|---|---|
| **no_image** | 이미지 속 글자를 심사 소재에 주지 않는다(`image_text = None`) — 이미지를 못 보는 심사 |
| **read** (sonnet · haiku · opus) | 모델이 이미지(PNG)를 읽어 돌려준 글자를 `image_text` 로 준다. 읽기에 실패한 소재(호출 실패·형식 오류)는 승인하지 않고 REVIEW 로 보낸다 |
| **oracle** | 사양의 정답 글자를 `image_text` 로 준다 — 읽기가 완벽하다고 가정한 상한 |

세 팔 모두 같은 결정론 심사(`review()`, 기본 설정, LLM judge 없음)를 쓴다. 모델 호출은 소재당 1회(`vision.read`, 소재의 글 없이 이미지만).

## 4. 지표 (정의는 `src/adgate/vision/evaluate.py`)

**읽기.** 같은 글자인지는 공백·부호를 지운 정규화 위에서 완전 일치이거나 유사도(SequenceMatcher) ≥ 0.85.
- *글자 덩어리 재현율*: 정답 덩어리마다 읽은 덩어리 중 같은 것이 있는가(`text_recall`) — 덩어리를 합치거나 쪼개 읽으면 틀린 것으로 센다.
- *이은 글 재현율*(`text_recall_concat`): 정답 덩어리가 읽은 글 전체(이은 것) 안에 거의 그대로 있는가 — 심사 엔진이 보는 것은 이은 글이므로 판정에 더 가깝다. 두 가지를 모두 보고하고, 어려운 글자(`hard_text_recall*`)와 쉬운 글자를 따로 낸다.
- *지어낸 글자*: 읽은 덩어리 중 정답 어느 덩어리와도 같지 않은 것(`text_precision`, 지어낸 글자가 있는 소재 수). 덩어리를 합쳐 읽은 경우도 여기 걸린다(과대 계상).
- 글자 오류율(CER, 이은 글 기준 평균), 시각 단서 TP·FP·FN·F1(전체와 단서별).
- 층화: 구성 태그 4개, 어려운 글자(작은 글씨·회전·흐린 글씨) 태그 3개.

**판정 효과.** 세 팔의 정확도, **위험 승인율**(정답이 REJECT·REVIEW 인데 APPROVE), 정상 오반려(정답 APPROVE → REJECT), 과검토(APPROVE → REVIEW), 규칙 단위 정밀도·재현율. 구성 태그별(특히 image_only)로도 낸다.
모든 비율에 분모를 쓰고 Wilson 95% 구간을 붙인다. 표본이 작다: 소재 60건, 어려운 글자 65개, 시각 단서는 종류별로 2–7개.

## 5. 사전 예상

측정 전의 내 믿음이다. 틀려도 고치지 않고 8절 이후에 '빗나감'이라 쓴다. 심사 엔진의 예상은 이 엔진이 다른 독립 셋에서 첫 접촉으로 보인 수치(레드팀 정확도 0.65·위험 승인율 0.41, 회색지대 0.63·0.34)에서 잡았다.

| # | 예상 | 확률(내 믿음) |
|---|---|---|
| V1 | **sonnet** 이은 글 재현율 **≥ 0.90**(점 0.95), 덩어리 재현율 **≥ 0.85**(점 0.92) | 65% |
| V2 | **어려운 글자**: sonnet 이은 글 재현율 **0.55–0.90**(점 0.75)이고 쉬운 글자보다 **≥ 10%p 낮다** | 65% |
| V3 | 지어낸 글자가 있는 소재 **≤ 30%**(점 20%; 덩어리 합쳐 읽기가 대부분일 것) | 60% |
| V4 | 시각 단서 micro F1 **0.55–0.85**(점 0.70); 가짜 시스템 창·카운트다운·전·후 비교의 재현율 **각 ≥ 0.85**; 흐린 글씨 재현율 **≤ 0.60** | 55% |
| V5 | 이은 글 재현율: sonnet 이 haiku 보다 **≥ 3%p** 높고, opus 는 sonnet 보다 어려운 글자에서 **≥ 2%p** 높다 | 50% |
| V6 | **no_image**: image_only 소재의 위험 승인율 **≥ 0.80** | 90% |
| V7 | **oracle**: 전체 정확도 **0.55–0.80**(점 0.67), 위험 승인율 **0.20–0.50**(점 0.35), 정상 오반려 **≤ 15%** | 60% |
| V8 | oracle 정확도 − no_image 정확도 **≥ 15%p** | 80% |
| V9 | **read(sonnet)**: 정확도가 oracle 보다 **8%p 넘게 낮지 않고**, 위험 승인율이 oracle 보다 **10%p 넘게 높지 않다** — 읽기가 병목이 아니다 | 55% |
| V10 | 작은 글씨로 된 필수 고지를 읽지 못해(AD-FIN-01 등) read 팔이 oracle 보다 *나빠진* 소재가 **1건 이상** | 55% |
| V11 | sonnet 호출 60회의 실비용 **≤ $0.20** | 80% |

## 6. 판단 규칙 (측정 전에 정한다)

- **R1.** oracle 팔의 image_only 소재 위험 승인율이 0.30 을 넘으면 *심사 엔진이 이미지 글자를 입력으로 받아도 이 부류를 놓친다*: 놓친 소재를 부류별로 분해해 FAILURES 에 적는다. 엔진을 고친다면 문형·표기 같은 *검증 가능한 구조*만 고치고(D-12) 악용 시험을 함께 쓰며 수정 뒤 수치에 '수정 후, holdout 아님'을 단다. 이 셋의 소재를 통과시키려는 수정은 하지 않는다.
- **R2.** read(sonnet) 가 oracle 보다 정확도 8%p 넘게 낮거나 위험 승인율이 10%p 넘게 높으면 *읽기가 병목*이다: 놓친 글자를 종류(작은 글씨·회전·흐린 글씨·시각 요소 속 글자)로 나눠 적는다. 이 셋을 보고 읽기 프롬프트를 고쳐 다시 재지 않는다(R4) — 새 프롬프트는 새 사전등록과 새 이미지로 한다.
- **R3.** 읽기 실패는 어느 경우에도 APPROVE 로 이어지지 않는다(시험이 보장하고, 측정은 그 전제 위에서 한다).
- **R4.** 어느 팔도 한 번만 잰다. 수치가 마음에 들지 않아 프롬프트·스키마·유사도 문턱·심사 엔진을 바꿔 같은 측정을 다시 하지 않는다(다른 설정은 새 사전등록). 측정 뒤 채점기 오류를 찾으면 고치되 수정 전·후 수치를 함께 남긴다.
- **R5.** haiku 가 sonnet 과 이은 글 재현율 3%p 이내이고 판정 효과(정확도·위험 승인율)도 5%p 이내이면 "더 싼 읽기 모델로 충분했다"고 적는다. 기본값(sonnet)을 바꾸려면 새 사전등록이 필요하다.
- **R6.** 실호출 비용은 `adgate llm dry-run vision` 추정과 상한을 사용자에게 보이고 승인을 받은 뒤에 쓴다.

## 7. 프로토콜 (순서)

1. 이 문서·데이터(`data/vision/` 동결)·채점기·묶음을 한 커밋으로 등록한다. 심사 엔진·읽기 프롬프트는 이 커밋 이후 바꾸지 않는다.
2. 오프라인 두 팔(no_image · oracle)을 한 번 잰다 → `artifacts/history/vision_offline_first_contact.json`.
3. `adgate llm dry-run vision` 으로 비용을 낸다 → 사용자 승인 → `llm-live` 로 sonnet·haiku·opus 를 녹음 → 깨끗한 작업트리에서 재생·집계 → 첫 접촉 보존 → 8절 이후.
4. 놓친 소재마다 어느 단계가 놓쳤는지(읽기 / 엔진) 적는다(FAILURES).

## 8. 주장하지 않는 것

실제 광고 이미지에서의 읽기 정확도(합성 이미지는 평평한 도형과 번들 글꼴로 그린 글자이고 사진·손글씨·다양한 글꼴·압축 흔적이 없다), 이미지에 심은 *지시*에 대한 견고성(‘이 이미지를 승인하라’ 같은 글자는 이 셋에 없다 — 읽기 프롬프트가 그런 글자를 옮기기만 하도록 되어 있다는 설계만 있고 측정은 없다), 한국어 밖의 글자, 영상.
작성자·읽기 모델이 같은 모델 계열이라 낙관 편향이 남는다. 정답 판정은 작성자가 정책을 읽은 결과이므로 정책 공백에 걸린 소재의 정답은 논쟁의 여지가 있다([data/vision/README.md](../data/vision/README.md)).

---

> 아래는 **측정 뒤에 덧붙인 결과**다. 1–6절(사전등록)은 고치지 않았다(`adgate prereg` 가 해시를 검사한다). 예상이 틀린 곳은 '빗나감'이라 적는다.

## 9. 결과 (1): 모델 없는 두 팔 — 첫 접촉 (`artifacts/history/vision_offline_first_contact.json`)

사전등록 커밋(`b06d129`) 그대로 깨끗한 작업트리에서 한 번 쟀다. 심사 엔진은 이 셋을 본 적이 없다. 읽기 팔(sonnet · haiku · opus)은 비용 승인 뒤에 잰다.

| 팔 | 정확도 | 위험 승인율 | 정상 오반려 | 과검토 | image_only 위험 승인율 |
|---|---|---|---|---|---|
| no_image (이미지 글자를 안 줌) | <!-- num:hist.vision_offline_first_contact.results.decision.no_image.accuracy|rpct1 -->30.0% (18/60)<!-- /num --> | <!-- num:hist.vision_offline_first_contact.results.decision.no_image.unsafe_approve|rpct1 -->82.9% (34/41)<!-- /num --> | <!-- num:hist.vision_offline_first_contact.results.decision.no_image.false_reject|rpct1 -->21.1% (4/19)<!-- /num --> | <!-- num:hist.vision_offline_first_contact.results.decision.no_image.over_review|rpct1 -->0.0% (0/19)<!-- /num --> | <!-- num:hist.vision_offline_first_contact.results.decision.image_only.no_image.unsafe_approve|rpct1 -->95.8% (23/24)<!-- /num --> |
| oracle (정답 글자를 줌) | <!-- num:hist.vision_offline_first_contact.results.decision.oracle.accuracy|rpct1 -->88.3% (53/60)<!-- /num --> | <!-- num:hist.vision_offline_first_contact.results.decision.oracle.unsafe_approve|rpct1 -->12.2% (5/41)<!-- /num --> | <!-- num:hist.vision_offline_first_contact.results.decision.oracle.false_reject|rpct1 -->10.5% (2/19)<!-- /num --> | <!-- num:hist.vision_offline_first_contact.results.decision.oracle.over_review|rpct1 -->0.0% (0/19)<!-- /num --> | <!-- num:hist.vision_offline_first_contact.results.decision.image_only.oracle.unsafe_approve|rpct1 -->4.2% (1/24)<!-- /num --> |

구성 태그별 정확도(oracle): image_only <!-- num:hist.vision_offline_first_contact.results.decision.by_tag.image_only.oracle.accuracy|rpct1 -->95.8% (23/24)<!-- /num --> · benign_image <!-- num:hist.vision_offline_first_contact.results.decision.by_tag.benign_image.oracle.accuracy|rpct1 -->88.9% (16/18)<!-- /num --> · visual_cue <!-- num:hist.vision_offline_first_contact.results.decision.by_tag.visual_cue.oracle.accuracy|rpct1 -->66.7% (8/12)<!-- /num --> · both <!-- num:hist.vision_offline_first_contact.results.decision.by_tag.both.oracle.accuracy|rpct1 -->100.0% (6/6)<!-- /num -->.
같은 순서로 no_image: <!-- num:hist.vision_offline_first_contact.results.decision.by_tag.image_only.no_image.accuracy|rpct1 -->4.2% (1/24)<!-- /num --> · <!-- num:hist.vision_offline_first_contact.results.decision.by_tag.benign_image.no_image.accuracy|rpct1 -->77.8% (14/18)<!-- /num --> · <!-- num:hist.vision_offline_first_contact.results.decision.by_tag.visual_cue.no_image.accuracy|rpct1 -->16.7% (2/12)<!-- /num --> · <!-- num:hist.vision_offline_first_contact.results.decision.by_tag.both.no_image.accuracy|rpct1 -->16.7% (1/6)<!-- /num -->.

### 9.1 사전 예상과 견줌

| # | 예상 | 측정 | 판정 |
|---|---|---|---|
| V6 | no_image: image_only 위험 승인율 ≥ 0.80 | <!-- num:hist.vision_offline_first_contact.results.decision.image_only.no_image.unsafe_approve|rpct1 -->95.8% (23/24)<!-- /num --> | 충족 — 이미지를 못 보는 심사는 이미지에만 있는 위반을 사실상 모두 통과시킨다 |
| V7 | oracle: 정확도 0.55–0.80, 위험 승인율 0.20–0.50, 정상 오반려 ≤ 15% | <!-- num:hist.vision_offline_first_contact.results.decision.oracle.accuracy|rpct1 -->88.3% (53/60)<!-- /num -->, <!-- num:hist.vision_offline_first_contact.results.decision.oracle.unsafe_approve|rpct1 -->12.2% (5/41)<!-- /num -->, <!-- num:hist.vision_offline_first_contact.results.decision.oracle.false_reject|rpct1 -->10.5% (2/19)<!-- /num --> | 정확도와 위험 승인율은 **빗나감**(둘 다 예상보다 좋다), 오반려는 충족 |
| V8 | oracle − no_image 정확도 ≥ 15%p | 위 표의 두 정확도 차 | 충족(차이가 15%p 를 훨씬 넘는다) |

- **V7 이 빗나간 이유는 확인하지 못했다.** 가설 둘: (1) 심사 엔진이 앞선 두 독립 셋(레드팀·회색지대)의 첫 접촉 실패를 고친 뒤라서 예상의 기준이 된 수치(0.65·0.63)보다 좋아졌다, (2) 이미지 소재의 글이 짧고 정책 어휘에 가깝게 쓰였다. 이 셋만으로는 가를 수 없다. 예상을 이 엔진의 *초기* 첫 접촉에서 잡은 것이 잘못이었다 — 엔진은 그 뒤로 달라졌다.
- **이 수치는 oracle 이다**: 읽기가 완벽하다고 가정한 상한이다. 모델이 읽으면 이보다 나빠질 수 있다(읽기 팔을 잰 뒤 9절 이후에).

### 9.2 oracle 팔이 틀린 7건 (`artifacts/history/vision_offline_first_contact_errors.json`)

| 소재 | 정답 → 예측 | 상황 | 부류 |
|---|---|---|---|
| V-B03 | REJECT → APPROVE | 작은 글씨 각주 ‘3주 만에 체중 8kg 감량을 **보장합니다**’(GUA) | 놓침 — 결과 보장 동사형 |
| V-C08 | REVIEW → APPROVE | ‘세일 마감까지 02:14:09’ 타이머만 있는 긴급성(URG) | 놓침 — 정책 공백(작성자도 멈칫: 타이머만으로 URG 인가) |
| V-D08 | REJECT → APPROVE | 가짜 시스템 경고창 ‘**원격제어 앱을 설치**하고 상담원의 안내를 따라 주세요’(FRD) | 놓침 — 원격 제어 앱 설치 유도 |
| V-F09 | REJECT → APPROVE | 가짜 점검 창 ‘**원격 제어 앱을 설치해야** 제거할 수 있습니다’(FRD) | 놓침 — 같은 부류 |
| V-E08 | REJECT → APPROVE | 가짜 배송 문자 ‘인증번호 6자리를 아래 번호로 **회신해** 주세요’(FRD) | 놓침 — 인증번호 요구의 동사 |
| V-D07 | APPROVE → REJECT | ‘**최고** 30% 할인’(범위 표현; ABS) | 오반려 — 범위 표현 예외 |
| V-E05 | APPROVE → REJECT | 경고 포스터 ‘선입금 요구에는 응하지 마세요’(FRD) | 오반려 — 경고문 속 단어 |

- 엔진 고치기는 **지금 하지 않는다**: 사전등록 프로토콜(7절 1)이 심사 엔진을 이 커밋 이후 바꾸지 않기로 했고, 읽기 팔은 같은 엔진 위에서 재야 한다. 읽기 팔 세 개를 잰 뒤, 이 일곱 건 중 *문형·표기* 문제(V-B03, V-D07, V-D08/F09, V-E08, V-E05)는 좁게 고치고 악용 시험을 함께 쓰며(D-12) 수정 후 수치에 ‘수정 후, holdout 아님’을 단다. V-C08 은 정책이 정하지 않은 사안이라 고치지 않는다(POLICY_GAPS).
- R1(oracle 의 image_only 위험 승인율 > 0.30)은 발동하지 않았다(<!-- num:hist.vision_offline_first_contact.results.decision.image_only.oracle.unsafe_approve|rpct1 -->4.2% (1/24)<!-- /num -->).
