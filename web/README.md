# NIGHTMARE LAB Demo UI

빌드 과정과 외부 자산이 없는 정적 데모 UI입니다. 브라우저에서 `index.html`을 열면 백엔드 연결을 먼저 시도하고, 사용할 수 없으면 결정적인 합성 케이크 사고 fixture로 자동 전환합니다.

## 실행

저장소 루트에서 정적 파일 서버를 실행합니다.

```powershell
python -m http.server 4173 --directory web
```

브라우저에서 <http://localhost:4173>을 엽니다.

백엔드를 함께 실행하면 동일 origin의 다음 endpoint를 사용합니다.

- `POST /api/runs`
- `GET /api/runs/{run_id}/events`

백엔드 계약은 [event-contract.md](event-contract.md)에 있습니다.

## 데모 동작

- 미션 입력은 한 번만 받습니다.
- 실행 중에는 승인이나 다음 단계 버튼이 없습니다.
- `CLONE → CRASH → AUTOPSY → VACCINE → REPLAY`가 이벤트에 따라 자동 진행됩니다.
- 메인 화면은 합성 세계의 피해와 복구를 설명합니다.
- 우측 Raw API Stream은 각 envelope의 `raw` 값을 그대로 렌더링합니다.
- 알 수 없는 이벤트 타입은 UI 상태를 바꾸지 않지만 Raw Stream에는 남습니다.
- `초기화`와 `같은 사고 재생`은 발표 리허설용입니다.

화면에 표시되는 지갑·주문·메일·캘린더·파일 접근 기록은 모두 합성 데이터이며 실제 외부 서비스와 연결되지 않습니다.

## 검사

Node.js 18 이상에서 reducer와 fixture 검사를 실행합니다.

```powershell
node web/tests/core.test.mjs
```

검사는 fixture 결정성, 보호 전·후 상태, 검증 결과, unknown event 보존을 확인합니다.
