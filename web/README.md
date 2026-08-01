# NIGHTMARE LAB Demo UI

> Implementation snapshot: the ratified D2 journey is
> [`../docs/frontend-journey-contract.md`](../docs/frontend-journey-contract.md) and
> the ratified D3 replacement wire is [`event-contract.md`](event-contract.md).
> The current production screen and request path below remain A4 migration evidence.

빌드 과정과 외부 자산이 없는 정적 데모 UI입니다. 브라우저에서 `index.html`을 열면 백엔드 연결을 먼저 시도하고, 사용할 수 없으면 결정적인 합성 케이크 사고 fixture로 자동 전환합니다.

## 실행

권장 경로는 저장소 루트에서 웹과 API를 단일 포트로 실행하는 것입니다.

```powershell
.\scripts\demo.ps1
```

브라우저에서 <http://127.0.0.1:8000>을 엽니다. API 경로가 정적 파일보다 우선하므로 같은 origin에서 `/api/runs`, `/health`, SSE를 그대로 사용합니다.

웹만 따로 개발할 때는 두 서버를 사용할 수 있습니다.

```powershell
$env:WEB_ORIGIN="http://localhost:5173"
uvicorn agent24.api.app:app --host 127.0.0.1 --port 8000
python -m http.server 5173 --directory web
```

브라우저에서 <http://localhost:5173/?api=http://localhost:8000>을 엽니다. `api` 쿼리를 생략하면 현재 origin의 API를 사용합니다.

웹은 다음 endpoint를 사용합니다.

- `POST /api/runs`
- `GET /api/runs/{run_id}/events`

실행 요청은 `{"input":"..."}`이며, SSE의 각 `data`는 `{run_id, seq, timestamp, type, payload, summary?}` envelope입니다. `payload`는 Raw Stream에 수정 없이 표시합니다. 자세한 계약은 [event-contract.md](event-contract.md)에 있습니다.

## 데모 동작

- GitHub repository URL, ref/commit, 미션을 한 번에 받고 하나의 자율 실행 입력으로 묶습니다.
- 실행 중에는 승인이나 다음 단계 버튼이 없습니다.
- `CLONE → CRASH → AUTOPSY → VACCINE → REPLAY`가 이벤트에 따라 자동 진행됩니다.
- 메인 화면은 합성 세계의 피해와 복구를 설명합니다.
- 우측 Raw API Stream은 각 envelope의 `raw` 값을 그대로 렌더링합니다.
- 알 수 없는 이벤트 타입은 UI 상태를 바꾸지 않지만 Raw Stream에는 남습니다.
- `unsupported`와 `budget_exhausted`는 과장된 안전 판정 없이 종료 사유를 표시합니다.
- `source_descriptor`의 immutable SHA는 live event일 때만 분석 대상 provenance에 반영합니다.
- `behavior_profile`의 retry/idempotency/reconciliation/untrusted-input/loop-budget 판정을 근거와 함께 표시합니다.
- `experiment_plan`의 선택 fault, 대상 tool, 선택 이유, 기대 evidence, seed/turn budget을 별도 카드로 표시합니다.
- `lab_report`가 도착하면 동결된 `LabReport`의 관찰·가설·제안·검증을 별도 칸에 표시합니다.
- `초기화`와 `같은 사고 재생`은 발표 리허설용입니다.

화면에 표시되는 지갑·주문·메일·캘린더·파일 접근 기록은 모두 합성 데이터이며 실제 외부 서비스와 연결되지 않습니다.

## 검사

Node.js 18 이상에서 reducer와 fixture 검사를 실행합니다.

```powershell
node web/tests/core.test.mjs
```

검사는 fixture 결정성, 보호 전·후 상태, 검증 결과, unknown event 보존을 확인합니다.
