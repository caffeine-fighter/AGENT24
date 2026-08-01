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
    "output": "{\"scenario_id\":\"life.payment_intent_timeout.v1\"}"
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
| `offline_demo` | external target이 없는 실행에서만 deterministic synthetic fixture로 전환 |
| `stage_failed` | source / diagnostic / OpenAI analysis / runtime 단계의 비종료 실패 |
| `run_failed` | 과거 producer 호환용 비종료 alias. 새 producer는 `stage_failed`를 사용 |
| `run_completed` | 유일한 종료 이벤트. stream의 마지막에 정확히 한 번만 존재 |

모든 `run_completed.payload`는 기존 `status`, `mode`와 함께 다음 실행 진실성 필드를
반드시 제공합니다.

```json
{
  "status": "openai_analysis_unavailable",
  "mode": "offline_demo",
  "source_resolved": true,
  "diagnostic_completed": true,
  "openai_analysis_completed": false,
  "execution_scope": "synthetic_archetype",
  "safety_boundary": "SIMULATION_ONLY"
}
```

`stage_failed`는 terminal이 아닙니다. 실패한 단계의 bounded `stage`, `code`, `message`만
기록하고, credential이나 provider exception 원문은 넣지 않습니다. source 또는
diagnostic 단계가 실패하면 관련 없는 fixture를 실행하지 않고 최종 `run_completed`로
닫습니다. controller 진단 뒤 OpenAI 단계만 실패한 경우에는 실제 controller report를
보존하고 `openai_analysis_completed=false`로 닫습니다.

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
| `source_snapshot` | 실제로 확인한 파일 범위·취득 방식·content/blob digest와 claim boundary 표시 |
| `target_profile` | `OWNER MANIFEST` 또는 `LAB-INFERRED STATIC PROFILE` provenance와 domain candidate 표시 |
| `pack_selection` | compatibility 상태, selected/candidate domain, WHY/EXPECT/evidence, 0-experiment fallback 표시 |
| `compatibility_report` | target code를 실행하지 않은 compatibility-only terminal report와 claim boundary 표시 |
| `behavior_profile` | `profile.BehaviorProfile`의 다섯 판정과 evidence/unknown reason 표시 |
| `pack.selected` | 어떤 domain Gym을 고를지에 대한 결정적 routing 판단과 그 근거 표시 |
| `experiment_plan` | typed `ExperimentPlan`의 선택 fault·근거·기대 evidence·budget 표시 |
| `gym.baseline.completed` | healthy twin의 seed·run digest·trace/ledger 개수 기록 |
| `gym.tool_call` / `gym.tool_result` | Life-v0 synthetic archetype의 원본 trace 보존 |
| `oracle.report` | controller-owned world·ledger·trace 불변식 판정 |
| `protected_replay` | 같은 seed의 SandboxGym baseline/perturbed/protected/control evidence |
| `finding_report` | 증거에서 유도된 정직한 terminal status와 artifact reference |
| `lab_report` | 동결된 Python `LabReport` payload를 관찰·가설·제안·검증 칸에 분리 표시 |

`source_descriptor.payload`는 `src/agent24/agent/source.py::SourceDescriptor`의
`model_dump(mode="json")` 결과입니다. `source: live` GitHub 입력은 `resolved_sha`가 정확히
40자인 full hexadecimal commit SHA일 때만 상단 target에 반영합니다. fixture/short SHA는
실제 resolve 결과로 승격하지 않고 Raw Stream에서만 감사할 수 있습니다.
`source_kind=local_bundle`이면 `source_path`와 `local://...` source URL을 함께 표시하며,
`revision_kind=bundle_sha256`과 64자 `bundle_sha256`을 source identity로 사용합니다.
source snapshot의 파일별 `blob_sha`와 `content_sha256`도 함께 보존하며, 어느 값도 Git
commit으로 표현하지 않습니다.

`target_profile.payload`는
`src/agent24/agent/participant_intake.py::ParticipantTargetProfile`의 JSON이다.
`provenance.origin`과 화면 레이블은 owner 선언과 Lab static 추론을 구분한다. static
evidence는 path, full source SHA, Git blob SHA, line selector만 포함하며 source 본문이나
target failure claim을 포함하지 않는다.

`pack_selection.payload`는 compatibility candidate를 #57 registry에 전달하는 seam이다.
static-only path에서는 `max_experiments=0`, `fallback=terminal_compatibility_only`이며
항상 compatibility-only다. owner manifest 경로에서도 이 이벤트는 provenance hand-off일 뿐,
실행 결정은 뒤따르는 별도 `pack.selected` payload가 소유한다.

`compatibility_report.payload`는 `experiments_run=0`, `findings=[]`를 schema로 강제한다.
Research candidate 같은 compatibility 주장을 실제 target vulnerability나 safety verdict로
표현하지 않도록 `claim_boundary`를 함께 표시한다.

`behavior_profile.payload`는 `src/agent24/agent/profile.py::BehaviorProfile`의
`model_dump(mode="json")` 결과입니다. live 이벤트의 `source_ref` 끝에 immutable hex SHA가
있을 때만 상단 resolved SHA에 반영합니다. fixture ref는 실제 resolve 결과로 승격하지 않습니다.

`pack.selected.payload`는 `src/agent24/agent/packs.py::PackSelection`의
`model_dump(mode="json")` 결과입니다. `behavior_profile` 직후, `experiment_plan`보다
**먼저** 나옵니다. unsupported로 끝나는 실행은 `experiment_plan`에 도달하지 못하므로,
이 이벤트가 "어떤 pack을 왜 골랐고 왜 아무것도 실행하지 않았는지"에 대한 유일한 기록입니다.

읽는 쪽이 반드시 구분해야 하는 두 가지가 있습니다.

- **선택된 pack ≠ 실행된 pack.** `stop`이 `null`일 때만 one-input controller가 그 pack을
  실제로 실행합니다. 현재 그 조건을 만족하는 것은 Life pack뿐이고, Research·Stock·Ticket·
  Adhoc은 선택은 되지만 `stop.detail`이 실행 경로를 담당하는 이슈 번호를 명시합니다.
- **`selected`가 `null`이면 판단이 갈린 것**입니다. `stop.reason`이 `insufficient_evidence`면
  두 domain pack이 동점이라 임의로 고르지 않고 종료한 경우이고, `unsupported_input`이면
  등록된 어느 pack의 tool surface에도 해당하지 않는 경우입니다.

`candidates`는 점수 순으로 정렬된 전체 후보이고, `selection_digest`는 같은 입력·registry
version에서 동일합니다. `why` / `expect` / `budget` / `fallback`은 표시용 요약이 아니라
감사 대상이므로 요약하지 않고 그대로 보존합니다.

`experiment_plan.payload`는 `src/agent24/agent/models.py::ExperimentPlan`의
`model_dump(mode="json")` 결과입니다. 웹은 첫 fault의 종류와 대상 도구뿐 아니라
`tool_choice_reason`, `expected_evidence`, scenario seed, `max_turns`, `single_variable`을
함께 표시해 왜 이 순서의 실험을 선택했는지 감사할 수 있게 합니다.

`lab_report.payload`는 `src/agent24/agent/models.py::LabReport`의
`model_dump(mode="json")` 결과입니다. 웹은 표시용 projection만 만들며 envelope의
원본 payload는 Raw API Stream에 그대로 보존합니다. `Finding.observed`, `diagnosis`,
`proposed_patch`, `verified`를 서로 대체하거나 합쳐서 주장하지 않습니다.

`finding_report.payload`와 모든 Gym event는 `execution_scope=synthetic_archetype`
경계를 따른다. pinned source에서는 manifest metadata만 읽고 repository code는 실행하지
않는다. 따라서 synthetic violation은 제출 Agent 자체의 측정된 취약점으로 표현하지 않는다.

## 웹 정규화 규칙

웹 reducer는 수신 경계에서만 다음 호환 변환을 합니다.

- `run_started` → `run.started`
- `run_completed` → `run.completed`
- `stage_failed` → `stage.failed`
- legacy `run_failed` / `run.failed` → `stage.failed`
- `source_descriptor` → `source.descriptor`
- `source_snapshot` → `source.snapshot`
- `target_profile` → `target.profile`
- `pack_selection` → `pack.compatibility`
- `compatibility_report` → `compatibility.report`
- `payload` → 상태 해석용 `data`
- `payload` → Raw Stream 표시용 `raw` — 객체 내용은 변경하지 않음
- wire 타입은 `wire_type`에 별도로 보존

기존 결정적 fixture의 `data` / `raw` envelope도 동일 reducer가 계속 지원합니다. 알 수 없는 타입은 UI 상태를 바꾸지 않지만 이벤트 목록과 Raw Stream에는 남습니다.

## D1 세 결과 축

표시용 reducer는 wire payload를 바꾸지 않고 `submission`, `investigation`,
`operation`을 서로 독립적으로 투영합니다.

- `submission`: request accepted, full-SHA pin, source/manifest preflight 실패
- `investigation`: profile/experiment 진행, measured, unsupported, no-failure, not-run
- `operation`: controller running, no-target offline fallback, partial OpenAI stage, terminal complete/failed

`github.resolve_ref`가 full SHA를 반환하지 않으면 submission은 `failed`,
investigation은 `not_run`, scope는 `none`입니다. external target 실행은 이후 내장
fixture로 우회하지 않습니다. `offline_demo` synthetic fixture는 target이 없는 generic
실행에만 허용됩니다. target의 controller 진단이 완료되고 OpenAI 설명만 불가능한 경우는
operation을 `partial/unavailable`로 표시하며 measured investigation과 controller report를
그대로 보존합니다.

## World shape

```json
{
  "wallet_krw": 451000,
  "orders": 1,
  "outbound_emails": 0,
  "calendar_events": 0,
  "files_touched": 0
}
```

추가 필드는 허용하며 기존 필드를 삭제하거나 의미를 바꾸지 않습니다.

## 연결 실패 시 내장 예시

웹은 먼저 `POST /api/runs`를 시도합니다. local static surface는 1.6초, hosted
surface는 bounded OpenAI planning을 위해 22초 안에 정상 `run_id`를 기다립니다.
첫 실행 기록을 받기 전에 API/SSE가 실패하면 동일 reducer에 내장 예시를 공급합니다.
돈 입력은 `life.payment_intent_timeout.v1`을 사용하고, 지원하지 않는 Surprise 입력은
결제 예시로 바꾸지 않은 채 `unsupported`로 끝냅니다. 내장 예시는 실제 외부 작업을
하지 않으며 화면에 “API에 연결하지 못해 내장 예시를 보여드려요. 제출한 저장소를
분석한 결과는 아니에요.”라고 표시됩니다. 입력값 오류 `422`는 내장 예시로 바꾸지 않고
폼 오류로 끝냅니다.
