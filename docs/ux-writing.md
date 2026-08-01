# NIGHTMARE LAB 한국어 문구 기준

화면 문구는 국내 사용자가 한 번에 이해할 수 있는 짧은 해요체를 기본으로 한다. 개발자용 원본 이벤트와 단계 코드처럼 제품을 설명하는 데 꼭 필요한 기술 용어만 그대로 둔다.

이 문서는 정보 구조와 한국어 UX의 수용 기준이다. terminal 상태의 제품 의미와 필수 안전
문구는 [`external-agent-contract.md`](external-agent-contract.md)가 소유하고, Raw API
event의 wire payload는 [`web/event-contract.md`](../web/event-contract.md)가 소유한다.
쉬운 다음 행동 문구는 계약의 정확한 terminal 문구를 보충할 수 있지만 대체하거나 의미를
약하게 만들 수 없다. Raw event의 type, 순서, payload value도 번역·요약해 덮어쓰지 않는다.

## 원칙

1. 화면 문구는 해요체로 통일한다.
2. `명사 + 명사`로 끝나는 상태 문구보다 누가 무엇을 하는지 드러나는 문장을 쓴다.
3. 버튼에는 누른 다음 일어날 일을 쓴다.
4. 한 문장에는 한 가지 내용만 담는다.
5. 실패를 숨기지 않되, 사용자가 다음에 할 일을 함께 알려준다.
6. 안전성과 분석 범위에 관한 문구는 짧게 줄이더라도 의미를 약하게 만들지 않는다.

## 이 제품에서 쓰는 표현

| 피할 표현 | 쓰는 표현 |
| --- | --- |
| 안전 실험 시작 | 실험 시작하기 |
| 입력 대기 / 실행 대기 | 입력 전 / 시작 전 |
| 보호책 | 안전장치 |
| 원본 API 이벤트 | 실시간 실행 기록 |
| 합성 세계 | 가상 환경 |
| 검증 결과 대기 | 다시 확인한 결과가 여기에 표시돼요 |
| 복사 실패 | 다시 시도 |

현재 P0 wire 단계명 `CLONE`, `CRASH`, `AUTOPSY`, `VACCINE`, `REPLAY`는 발표와 실행
로그에서 쓰는 고유 식별자이므로 유지한다. 화면에서는 각각 `환경 준비`, `실패 재현`,
`원인 분석`, `안전장치 적용`, `다시 확인`을 먼저 보여준다. source pin과 profile provenance는
`환경 준비` 결과 가까이에 별도로 보여주며, wire 단계가 바뀌면 event contract와 이 문서를
같이 갱신한다.

## 첫 화면 10초 gate

제품 작업에 참여하지 않은 평가자를 최소 두 명 모집해 desktop `1440 × 900`과 mobile
`320 × 568`에 한 명 이상씩 배정한다. 각 평가자는 사전 설명과 다른 viewport 노출 없이
한 viewport만 처음 본다. 화면을 보여준 뒤 도움말이나 실행 기록을 열지 않고, 최초
viewport에서 아래로 한 번 이어 읽는 것만 허용하며 form 제출이나 다른 상호작용은 하지
않는다. 정확히 10초 뒤 화면을 가리고 다음 세 질문을 표의 문구 그대로 묻는다.

| 질문 | 반드시 전달할 의미 |
|---|---|
| 무엇을 입력하나요? | GitHub 저장소, 기준 branch/commit, Agent에게 시킬 일 |
| 무엇을 받나요? | 가상 환경에서 실패 가능성을 시험한 결과. 실험하지 못함·실패 미관찰을 구분하고, 실패를 관찰했을 때만 원인·안전장치·재확인 결과를 제공 |
| 실제로 무엇을 하지 않나요? | 저장소 코드를 실행하지 않고 실제 주문·결제·외부 서비스 행동도 하지 않음 |

평가 진행자가 제품 용어를 미리 설명하거나 정답 후보를 제시하면 무효다. 답변 시간은
10초 노출 시간에 포함하지 않지만, 모든 평가자가 세 의미를 자기 말로 답하고 첫 행동으로
저장소·버전·할 일을 입력한 뒤 `실험 시작하기`를 선택한다고 말해야 통과한다. 결과를
“취약점 인증” 또는 “안전 보증”으로 이해하면 실패다. 자동화는 노출 전제만 검증하며 사람의
이해를 통과했다고 대신 판정하지 않는다.

두 viewport 모두 제품 가치, simulation 경계, form 시작을 최초 viewport에 보여준다.
primary action은 mobile에서도 최초 viewport 높이 한 번 이내의 아래쪽 scan으로 도달한다.

첫 화면의 시각 순서는 다음 의미를 지킨다.

1. 제품이 하는 일과 가상 실험 경계를 먼저 보여준다.
2. 저장소 → 기준 버전 → 시킬 일 순서로 한 입력 form을 보여준다.
3. 버튼은 form의 다음 행동으로 보이고, 결과나 실행 기록보다 먼저 탐색된다.
4. 자세한 기술 evidence와 Raw Stream은 결과의 근거로 남기되 첫 행동을 가리지 않는다.

## 상태별 문구와 다음 행동

각 상태는 `무슨 일이 있었는지 → 어떤 범위까지 확인했는지 → 다음에 무엇을 할지` 순서로
읽힌다. 색, badge 또는 내부 status code만으로 상태를 전달하지 않는다.

| 상태 | 화면에서 먼저 답할 것 | 보이는 다음 행동 | 금지하는 오해 |
|---|---|---|---|
| 시작 전 | 입력 세 항목과 가상 실험 범위 | `실험 시작하기` | 실제 저장소 코드나 외부 서비스를 실행함 |
| 입력 오류 | 어느 항목이 왜 유효하지 않은지 | 해당 항목을 고치는 방법 | 서버 오류처럼 표현하거나 입력을 지움 |
| 정상 실행 중 | 현재 하는 일과 아직 끝나지 않았다는 사실 | 기다리거나 실행 기록 확인 | 중간 결과를 완료로 표현함 |
| 정상 완료 | 관찰한 사실, 추정 원인, 제안한 안전장치, 다시 확인한 범위 | 같은 조건으로 다시 실험하거나 evidence 확인 | 실패 미발견·한 seed 통과를 안전 인증으로 표현함 |
| 지원하지 않음 | 이 작업에 맞는 실험이 없어 **실험하지 않았음** | 지원되는 mission으로 바꾸거나 지원 범위 확인 | 문제 없음, 분석 완료, 다른 domain의 성공으로 치환함 |
| source preflight 실패 | 저장소/ref 접근·manifest 중 무엇을 확인하지 못했는지와 외부 Agent 진단을 주장하지 않는다는 경계 | 입력·공개 범위·ref 또는 서버 token 권한 확인 | 제출 대상을 분석했거나 fallback 결과가 제출 대상의 결과라고 표현함 |
| client request/첫 event 전 SSE 실패 | 제출 대상의 분석 결과를 확인하지 못했고 명시적 built-in fixture로 전환했음 | 예시임을 알고 탐색하거나 연결을 확인해 다시 시도 | 내장 예시를 제출 대상의 결과로 표현함 |
| event 수신 뒤 valid terminal 전 SSE 단절 | 받은 기록은 보존하지만 신뢰할 수 있는 terminal 결과는 아직 없음 | 연결을 확인하고 같은 입력으로 다시 시도 | 부분 기록을 완료·미지원·내장 예시 terminal로 추정함 |
| 모델 진단 unavailable/failed | 모델 tool loop는 완주하지 않았고 표시된 evidence는 명시적인 same-target deterministic reference 결과임 | `stage_failed`, `planner.comparison`, reference evidence와 Raw Stream 확인 | unrelated fixture, live 성공, 모델이 만든 결과로 표현함 |
| 진단 loop 실패 | 외부 Agent 결과를 만들지 못했고 다른 fixture로 전환하지 않았음 | Raw Stream에서 축약된 원인을 확인하고 재시도 | 정상 진단 완료 또는 target finding으로 표현함 |

지원하지 않음과 각 실패 상태는 terminal까지 끝나더라도 성공 결과처럼 보이면 안 된다.
`완료`라는 단어를 쓸 때는 `지원하지 않음`, `분석하지 않음`, `offline`, `fallback` 경계를
같은 viewport와 status announcement에서 함께 제공한다. 친절한 다음 행동을 덧붙여도
[`external-agent-contract.md`의 정확한 사용자 문구](external-agent-contract.md#상태와-정확한-사용자-문구)를
줄이거나 바꾸지 않는다.

## 기술 용어 경계

| 위치 | 허용하는 표현 | 피하는 표현 |
|---|---|---|
| 입력·상태·결과의 기본 화면 | 저장소, full commit SHA, provenance, 실험, 확인한 사실, 추정한 원인, 안전장치, 다시 확인 | SSE, terminal, payload, pack ID, fixture ID, cost unit, 내부 reason code |
| profile 근거 | `OWNER MANIFEST`, `LAB-INFERRED STATIC PROFILE`, `Observed Behavior Profile / 관찰 가능한 행동 프로필` | `EXECUTABLE`, `TERMINAL`, 성격 분석, 안전 점수 |
| 안전·출처 badge | 한국어 의미를 먼저 둔 `SIMULATION`, `SYNTHETIC ARCHETYPE`, `fixture_fallback`, `AUTO / FIXTURE` 보조 audit label | 영문 badge만으로 실제 동작 없음·내장 예시 경계를 전달 |
| 단계 rail | 한국어 단계명을 먼저 표시하고 `CLONE` 등 고유 ID를 보조로 표시 | 영문 단계 ID만 표시 |
| 실시간 실행 기록 | 원본 event type, `tool_call`, `tool_result`, JSON과 run ID 유지 | Raw payload를 자연어로 바꾸거나 일부 field를 삭제 |
| 오류 안내 | 사용자가 확인할 입력·연결과 정직한 분석 범위 | provider 예외 전문, stack trace, credential 또는 임의 내부 code |

`GitHub`, commit SHA, API처럼 사용자가 실제 입력이나 연결 문제를 해결하는 데 필요한
용어는 쓸 수 있다. 같은 뜻을 한국어로 먼저 설명하고, 개발자용 식별자는 보조 위치에 둔다.

## `web/`·`site/` 문구 parity

local `web/`와 hosted `site/public/demo/`는 다음 user-observable contract가 같아야 한다.

- 첫 화면의 section 순서, form label, button, safety scope와 landmark/ARIA 이름
- 시작 전, 입력 오류, 정상, unsupported, source preflight, client transport, offline/fallback의 status와 다음 행동
- result claim boundary와 Raw Stream을 별도 evidence surface로 두는 구조
- mobile breakpoint에서 읽는 순서와 의도적으로 scroll 가능한 영역
- 같은 입력·fixture를 재생할 때 번역 projection과 무관한 Raw event type, 순서, payload value identity

parity test는 다음 semantic key를 두 tree에서 같은 state별로 비교한다. shared copy map이나
rendered role/accessibility name 비교를 사용할 수 있지만 whole-file byte 비교에 의존하지 않는다.

| semantic key | 비교할 값 |
|---|---|
| input | repository, ref, mission label과 accessible name |
| primary action | 시작 button name과 enabled/running state |
| safety boundary | simulation·외부 코드 미실행 의미 |
| status | idle, input error, running, terminal category와 human-readable announcement |
| terminal boundary | 정상·unsupported·fallback에서 허용하는 claim |
| recovery action | 수정, 재시도, evidence 확인 중 해당 상태에 보이는 행동 |
| evidence | 결과 projection과 Raw Stream landmark의 분리 |

두 tree 전체의 byte identity는 요구하지 않는다. `docs/verification.md`에 기록된 hosted
network timeout처럼 동작 환경 때문에 필요한 차이는 허용하지만, 사용자 문구·상태 의미·DOM
탐색 순서·접근성 이름을 바꾸는 근거가 되지 않는다. 한 tree의 시작 전 문구나 terminal
문구만 바꾼 negative fixture가 parity gate를 실제로 실패시켜야 한다.

시작 전 primary surface에는 `payment.charge`, patch code, seed/scenario ID, cake·wallet처럼
실행 결과로 오해할 evidence를 미리 보여주지 않는다. 입력 전 placeholder는 입력 안내와
simulation 경계만 전달한다.

## 320 px와 접근성 gate

responsive acceptance는 다음 세 profile을 독립적으로 확인한다.

- desktop: viewport `1440 × 900`, browser zoom 100%
- mobile: viewport `320 × 568`, browser zoom 100%
- zoom: viewport `1440 × 900`에서 browser zoom 200%

keyboard-only 경로도 별도로 확인한다. 통과 조건은 다음과 같다.

- 각 profile에서 `document.documentElement.scrollWidth <= clientWidth`이고 required element의
  bounding box가 viewport 또는 의도한 내부 scroller 안에 있다. `overflow-x: hidden`으로
  잘린 내용을 가리는 것은 통과가 아니다.
- phase rail과 Raw Stream처럼 의도한 내부 scroller만 가로/세로 scroll을 가질 수 있으며
  focus를 받은 뒤 arrow/Page key로 scroll 위치가 바뀌고 다음 Tab으로 빠져나올 수 있다.
- 제품 가치와 simulation 경계, form 시작이 첫 viewport에 보이고 primary action은 한 방향의
  vertical scroll로 도달한다.
- project touch target은 WCAG 2.2 AA의 `24 × 24` 최소치보다 엄격한 `44 × 44` CSS px이고,
  keyboard-visible focus indicator는 인접한 색과 `3:1` 이상 대비된다.
- form 내부의 상대적 keyboard focus는 저장소 → 기준 버전 → 할 일 → 시작 → 보이는 보조
  행동 순서다. page 전체의 고정 Tab 횟수는 계약하지 않는다. Enter는 한 번만 submit하고,
  실행 중 시작 control은 중복 submit을 막으며 focus가 임의로 이동하지 않는다.
- 입력 오류는 `role="alert"`로 읽히고 첫 invalid field가 focus를 받는다. field에는
  `aria-invalid="true"`와 오류 설명을 연결한 `aria-describedby`를 함께 둔다.
- 진행·terminal status는 bounded human-readable live region에 각 semantic 전환마다 한
  번 반영된다. DOM mutation 자동화와 manual screen-reader 확인을 분리하며, Playwright
  assertion만으로 실제 낭독 횟수를 증명했다고 주장하지 않는다. 시각적 Raw Stream은
  완전하게 보존하되 raw payload 자체를 live announcement로 event마다 다시 읽게 하지 않는다.
- 정보는 색만으로 구분하지 않고, `prefers-reduced-motion`에서 의미 없는 motion을 제거한다.
- 일반 text는 WCAG 2.2 AA contrast `4.5:1`, 큰 text는 `3:1` 이상이다. control·state를
  식별하는 데 필요한 UI 경계와 의미 있는 graphic도 인접 색과 `3:1` 이상 대비된다.
- 시작 전, 정상, unsupported, pre-event client fallback에서 automated accessibility scan의
  `serious` 또는 `critical` violation이 0건이다.

## #83 구현 evidence

A/Web 구현 PR은 같은 commit에서 다음 evidence를 남긴다.

- 번역투·금지 용어 regression과 시작 전을 포함한 `web/`·`site/` copy/Raw parity test
- idle/input error, normal, typed unsupported, pre-event client fallback real-browser path의
  상태 문구와 다음 행동
- desktop, 320 px, desktop 200% zoom에서 landmark, focus order, alert/status, overflow assertion
- 시작 전·정상·unsupported·fallback 상태의 serious/critical accessibility violation 0건
- zero-context 10초 test의 날짜, viewport, 익명 evaluator 수, 질문별 pass/fail

real-browser gate의 최소 observable outcome은 다음과 같다. “보임”은 render되고 다른 element에
가려지지 않으며, viewport 조건이 있으면 그 bounding box 안에 있다는 뜻이다.

| path | 반드시 관찰할 결과 |
|---|---|
| 시작 전 | result처럼 보이는 payment/cake/wallet/patch evidence 0건, 입력 세 항목과 safety boundary, enabled primary action |
| 입력 오류 | run 생성·fixture 재생 0건, 입력값 보존, 연결된 corrective message, 첫 invalid field focus |
| 정상 | submit 1회, terminal 1회, non-fallback boundary, 결과 scope와 evidence/replay action |
| typed unsupported | typed terminal 1회, “실험하지 않음” 경계, payment/cake experiment evidence 0건, 수정·지원 범위 action |
| pre-event fallback | visible `fixture_fallback`와 `AUTO / FIXTURE`, 제출 대상 분석 결과 미확인 경계, terminal 1회, 수정·재시도 action |

자연스러운 한국어 자동 gate는 rendered product projection에서만 bounded forbidden-term corpus를
검사한다. Raw Stream과 developer evidence는 제외한다. 수동 cold-reader gate가 문맥과 어조를
확인하며, whole sentence·문장부호·줄바꿈·pixel 위치·DOM class·page 높이·animation 시간·
provider 예외 문구·network timing·screenshot hash는 동결하지 않는다. 대신 semantic state,
safety truth boundary, required action, accessible name, 상대적 reading/focus order와 unedited Raw
Stream contract를 동결한다.

자동화 결과는 실제 credential, repository/ref/mission 원문, Raw event, screenshot, video,
trace 또는 browser dump를 CI artifact에 넣지 않는다. UI screenshot이 별도로 필요하면
의도적으로 캡처하고 Raw Stream, 개인 데이터, credential과 local browser chrome을 제거한다.

## 참고 기준

- [토스의 8가지 라이팅 원칙](https://toss.tech/article/21022): 다음 동작을 예상할 수 있게 쓰기, 의미 없는 단어와 문장 덜어내기, 소리 내어 읽어도 쉬운 표현 쓰기.
- [앱인토스 UI/UX 가이드](https://developers-apps-in-toss.toss.im/design/consumer-ux-guide.html): 해요체, 능동형, 캐주얼한 경어, 명사 나열 피하기, 오류 상황을 명확히 설명하기.
- [WCAG 2.2 Contrast (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html): 일반 text `4.5:1`, 큰 text `3:1`의 AA 기준.
- [WCAG 2.2 Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html): control·state·의미 있는 graphic의 필요한 시각 정보는 인접 색과 `3:1` 이상 대비.
- [WCAG 2.2 Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html): AA 최소치는 `24 × 24` CSS px이며, 이 프로젝트는 touch surface에 더 엄격한 `44 × 44` gate를 사용.

새 문구를 추가할 때는 소리 내어 읽어 보고, 같은 뜻을 더 짧고 구체적인 동작 문장으로 쓸 수 있는지 확인한다.
