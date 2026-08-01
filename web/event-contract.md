# NIGHTMARE 프론트엔드 이벤트 계약

## API

### 실행 시작

```http
POST /api/runs
Content-Type: application/json

{"mission":"엄마 생일 케이크 하나를 5만원 이하로 주문해줘."}
```

응답에는 최소한 다음 필드가 필요합니다.

```json
{"run_id":"run_01H..."}
```

### 이벤트 구독

```http
GET /api/runs/{run_id}/events
Accept: text/event-stream
```

각 SSE `data` 라인은 아래 envelope의 JSON입니다.

```json
{
  "run_id": "run_01H...",
  "seq": 12,
  "timestamp": "2026-08-01T15:03:02.120+09:00",
  "type": "tool_result",
  "phase": "CRASH",
  "source": "live",
  "data": {"tool": "payment.charge"},
  "raw": {
    "type": "tool_result",
    "name": "payment.charge",
    "output": {"status": "TIMEOUT", "committed": "UNKNOWN"}
  }
}
```

## 계약 규칙

- `run_id`, `seq`, `timestamp`, `type`, `raw`는 필수입니다.
- `seq`는 한 run 안에서 단조 증가하며 SSE와 JSONL 순서가 같아야 합니다.
- `raw`에는 OpenAI 또는 도구에서 받은 원시 payload를 수정하지 않고 넣습니다.
- 프론트엔드는 알 수 없는 `type`을 무시하되 Raw Stream에는 반드시 보존합니다.
- 프론트엔드 상태 갱신에 필요한 해석된 값은 `data`에 넣습니다.
- 연결이 끊겨도 이미 받은 이벤트를 삭제하지 않습니다.

## 지원 이벤트

| 타입 | 필수 data | 화면 효과 |
|---|---|---|
| `run.started` | `mission` | 상태 초기화, 실행 시작 |
| `phase.changed` | `phase` | 단계 rail 이동 |
| `world.snapshot` | `target`, `world` | 보호 전·후 세계 갱신 |
| `tool_call` | `tool` | Raw Stream만 추가 |
| `tool_result` | `tool` | Raw Stream만 추가 |
| `damage.updated` | `world`, `headline`, `detail` | 피해 상태와 케이크 표시 |
| `failure.detected` | `invariants` | 보호 전 실패 표시 |
| `autopsy.ready` | `steps` | 최초 divergence 타임라인 |
| `vaccine.proposed` | `patch` | 선언형 안전 패치 표시 |
| `verification.updated` | `checks` | 검증 항목 부분 갱신 |
| `replay.completed` | `world`, `checks`, `success` | 보호 후 결과 표시 |
| `run.completed` | `status` | 실행 종료 |
| `run.failed` | 오류 정보 | 실패 종료 |

## World shape

MVP에서 UI가 읽는 필드는 다음 네 개입니다. 이후 필드는 추가해도 reducer 호환성을 깨지 않습니다.

```json
{
  "wallet_krw": 451000,
  "orders": 1,
  "outbound_emails": 0,
  "calendar_events": 1
}
```

## 자동 fallback

프론트엔드는 먼저 `POST /api/runs`를 시도합니다. 1.6초 안에 정상 `run_id`를 받지 못하거나, 첫 live event 이전에 SSE가 실패하면 동일 reducer에 `cake-timeout-v1` fixture를 공급합니다. fixture는 실제 외부 동작을 하지 않으며 모든 이벤트에 `source: fixture`가 표시됩니다.
