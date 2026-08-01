# Raw event boundary

`EventManager`가 한 번 만든 envelope를 그대로 JSONL에 append하고 SSE queue로 fan-out한다. 따라서 두 경로의 `run_id`, `seq`, 순서가 같다.

```json
{
  "run_id": "...",
  "seq": 1,
  "timestamp": "2026-08-01T00:00:00+00:00",
  "type": "tool_call",
  "payload": {"type": "function_call", "name": "inspect_target", "arguments": "..."},
  "summary": "inspect_target"
}
```

`tool_call`/`tool_result`의 `payload`는 Agents SDK `raw_item`을 `model_dump(mode="json", exclude_none=False)`로 직렬화한 값이다. 원본을 요약·재작성하지 않으며, 표시용 요약은 envelope의 별도 `summary` 필드에만 둔다.

소비자는 `GET /api/runs/{run_id}/events` 또는 `artifacts/raw-streams/<run_id>.jsonl`을 사용한다. API key는 이벤트에 넣지 않는다.

SSE는 모든 envelope를 기본 `message` 이벤트의 `data:`로 보낸다. 의미 타입은 이미 JSON의 `type`에 있으므로 별도 `event:` 라인을 사용하지 않는다. 이 방식은 브라우저의 단일 `onmessage` 경로가 앞으로 추가될 알 수 없는 타입까지 Raw Stream에 보존하게 한다.

## Terminal and stage-failure policy

`run_completed`만 terminal이며 stream 마지막에 정확히 한 번 기록한다. `stage_failed`와
legacy `run_failed`는 non-terminal이라서, 안전하게 분류한 실패를 기록한 뒤 하나의
`run_completed`로 닫을 수 있다. terminal payload는 항상
`source_resolved`, `diagnostic_completed`, `openai_analysis_completed`,
`execution_scope`를 포함한다.

External target의 live Raw Stream에는 다섯 Responses function item
(`inspect_target`, `list_experiments`, `run_sandbox_experiment`, `inspect_evidence`,
`verify_mitigation`)과 대응하는 다섯 output item이 SDK 순서 그대로 남는다. controller가
추가하는 `diagnostic.*`, `planner.*`, `gym.*` event는 raw item을 대체하지 않는 별도
감사 projection이다.

External target의 source 실패는 실험 없이 종료한다. OpenAI key/timeout/provider/controller
실패는 generic `inspect_synthetic_gym` fixture로 바꾸지 않고, `stage_failed`와
`planner.comparison(fallback_policy=same_target_reference)` 뒤 같은 target의 deterministic
reference plan만 실행한다. target이 없는 명시적 `no_target_offline_demo` 실행만 synthetic
fallback tool events를 만들 수 있다.
