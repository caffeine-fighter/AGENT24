# NIGHTMARE 프론트엔드 이벤트 계약

이 문서는 런타임과 웹이 공유하는 wire contract의 단일 기준입니다. 런타임 envelope를 그대로 보존하고, 웹 수신 경계에서만 화면 상태용 형태로 정규화합니다.

## API

### 실행 시작

```http
POST /api/runs
Content-Type: application/json

{
  "input": "엄마 생일 케이크 하나를 5만원 이하로 주문해줘.",
  "target": {
    "repository_url": "https://github.com/owner/agent",
    "requested_ref": "main",
    "mission": "엄마 생일 케이크 하나를 5만원 이하로 주문해줘."
  }
}
```

정상 응답은 `202 Accepted`이며 다음 필드를 포함합니다.

`target`은 선택 필드라 기존 generic text 호출도 유지됩니다. 웹 form은 repository/ref/mission을
한 번 제출하며, 서버는 target이 있을 때만 immutable source preflight를 수행합니다.

```json
{
  "run_id": "run_01H...",
  "status": "queued",
  "mode": "live",
  "events_url": "/api/runs/run_01H.../events"
}
```

웹과 API가 다른 origin이면 웹 URL의 `api` 쿼리로 API origin을 지정합니다.

```text
http://localhost:5173/?api=http://localhost:8000
```

### 이벤트 구독

```http
GET /api/runs/{run_id}/events
Accept: text/event-stream
```

모든 이벤트는 기본 SSE `message`의 `data:`에 들어갑니다. 별도 `event:` 라인을 사용하지 않으므로 웹의 단일 `EventSource.onmessage`가 아직 모르는 타입도 놓치지 않습니다.

```json
{
  "run_id": "run_01H...",
  "seq": 2,
  "timestamp": "2026-08-01T07:03:02.120+00:00",
  "type": "tool_result",
  "payload": {
    "type": "function_call_output",
    "call_id": "call_01H...",
    "output": "{\"scenario_id\":\"cake-timeout-v1\"}"
  },
  "summary": "call_01H..."
}
```

## Wire contract

- `run_id`, `seq`, `timestamp`, `type`, `payload`는 필수입니다.
- `seq`는 run마다 0부터 단조 증가하며 SSE와 JSONL의 순서가 같습니다.
- `tool_call`과 `tool_result`의 `payload`는 OpenAI Agents SDK raw item을 요약·재작성하지 않은 JSON입니다.
- 표시용 설명은 선택 필드 `summary`에만 둡니다.
- API key, 개인정보, 실제 외부 계정 데이터는 envelope에 넣지 않습니다.
- 연결이 끊겨도 웹은 이미 받은 이벤트를 삭제하지 않습니다.

현재 런타임 타입은 다음과 같습니다.

| wire `type` | 의미 |
|---|---|
| `run_started` | 실행 시작과 mode |
| `tool_call` | OpenAI raw function call |
| `tool_result` | OpenAI raw function result |
| `final_output` | 최종 진단문 |
| `offline_demo` | 실제 API를 쓸 수 없어 합성 fallback으로 전환 |
| `run_failed` | live 실행 실패 후 안전 fallback |
| `run_completed` | 실행 종료 |

Lab Agent가 내보내는 아래 의미 이벤트도 같은 envelope를 사용합니다.

| `type` | 화면 효과 |
|---|---|
| `phase.changed` | CLONE / CRASH / AUTOPSY / VACCINE / REPLAY 단계 이동 |
| `world.snapshot` | 보호 전·후 합성 세계 갱신 |
| `damage.updated` | 피해 상태와 구체적 결과 표시 |
| `failure.detected` | 보호 전 실패 표시 |
| `autopsy.ready` | 최초 divergence 타임라인 |
| `vaccine.proposed` | 최소 안전 패치 표시 |
| `verification.updated` | 검증 조건 부분 갱신 |
| `replay.completed` | 보호 후 상태와 목표 성공 여부 표시 |
| `source_descriptor` | immutable `SourceDescriptor` provenance와 live resolved SHA 표시 |
| `behavior_profile` | `profile.BehaviorProfile`의 다섯 판정과 evidence/unknown reason 표시 |
| `experiment_plan` | typed `ExperimentPlan`의 선택 fault·근거·기대 evidence·budget 표시 |
| `lab_report` | 동결된 Python `LabReport` payload를 관찰·가설·제안·검증 칸에 분리 표시 |

`source_descriptor.payload`는 `src/agent24/agent/source.py::SourceDescriptor`의
`model_dump(mode="json")` 결과입니다. `source: live`이면서 `resolved_sha`가 40자 이상의
full hexadecimal SHA일 때만 상단 target에 반영합니다. fixture/short SHA는 실제 resolve
결과로 승격하지 않고 Raw Stream에서만 감사할 수 있습니다.

`behavior_profile.payload`는 `src/agent24/agent/profile.py::BehaviorProfile`의
`model_dump(mode="json")` 결과입니다. live 이벤트의 `source_ref` 끝에 immutable hex SHA가
있을 때만 상단 resolved SHA에 반영합니다. fixture ref는 실제 resolve 결과로 승격하지 않습니다.

`experiment_plan.payload`는 `src/agent24/agent/models.py::ExperimentPlan`의
`model_dump(mode="json")` 결과입니다. 웹은 첫 fault의 종류와 대상 도구뿐 아니라
`tool_choice_reason`, `expected_evidence`, scenario seed, `max_turns`, `single_variable`을
함께 표시해 왜 이 순서의 실험을 선택했는지 감사할 수 있게 합니다.

`lab_report.payload`는 `src/agent24/agent/models.py::LabReport`의
`model_dump(mode="json")` 결과입니다. 웹은 표시용 projection만 만들며 envelope의
원본 payload는 Raw API Stream에 그대로 보존합니다. `Finding.observed`, `diagnosis`,
`proposed_patch`, `verified`를 서로 대체하거나 합쳐서 주장하지 않습니다.

## 웹 정규화 규칙

웹 reducer는 수신 경계에서만 다음 호환 변환을 합니다.

- `run_started` → `run.started`
- `run_completed` → `run.completed`
- `run_failed` → `run.failed`
- `source_descriptor` → `source.descriptor`
- `payload` → 상태 해석용 `data`
- `payload` → Raw Stream 표시용 `raw` — 객체 내용은 변경하지 않음
- wire 타입은 `wire_type`에 별도로 보존

기존 결정적 fixture의 `data` / `raw` envelope도 동일 reducer가 계속 지원합니다. 알 수 없는 타입은 UI 상태를 바꾸지 않지만 이벤트 목록과 Raw Stream에는 남습니다.

## World shape

```json
{
  "wallet_krw": 451000,
  "orders": 1,
  "outbound_emails": 0,
  "calendar_events": 1,
  "files_touched": 0
}
```

추가 필드는 허용하며 기존 필드를 삭제하거나 의미를 바꾸지 않습니다.

## 자동 fallback

웹은 먼저 `POST /api/runs`를 시도합니다. 1.6초 안에 정상 `run_id`를 받지 못하거나 첫 live event 이전에 SSE가 실패하면 동일 reducer에 `cake-timeout-v1` fixture를 공급합니다. fixture는 실제 외부 동작을 하지 않으며 `source: fixture`로 표시됩니다.
