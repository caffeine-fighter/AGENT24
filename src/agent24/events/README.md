# Raw event boundary

`EventManager`가 한 번 만든 envelope를 그대로 JSONL에 append하고 SSE queue로 fan-out한다. 따라서 두 경로의 `run_id`, `seq`, 순서가 같다.

```json
{
  "run_id": "...",
  "seq": 1,
  "timestamp": "2026-08-01T00:00:00+00:00",
  "type": "tool_call",
  "payload": {"type": "function_call", "name": "inspect_synthetic_gym", "arguments": "..."},
  "summary": "inspect_synthetic_gym"
}
```

`tool_call`/`tool_result`의 `payload`는 Agents SDK `raw_item`을 `model_dump(mode="json", exclude_none=False)`로 직렬화한 값이다. 원본을 요약·재작성하지 않으며, 표시용 요약은 envelope의 별도 `summary` 필드에만 둔다.

소비자는 `GET /api/runs/{run_id}/events` 또는 `artifacts/raw-streams/<run_id>.jsonl`을 사용한다. API key는 이벤트에 넣지 않는다.
