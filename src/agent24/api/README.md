# API boundary

현재 구현은 한 번의 입력을 `Runner.run_streamed`에 넘기는 얇은 FastAPI 경계다.

- `POST /api/runs` — `{"input": "..."}`을 받아 `202`와 `run_id`/`events_url`을 반환한다.
- `GET /api/runs/{run_id}/events` — 같은 이벤트를 SSE로 replay하고 live fan-out한다.
- `GET /health` — `mode: live | offline_demo`와 키 설정 여부만 보여준다. 키 값은 반환하지 않는다.

라이브 모드에서 `OpenAIProvider`가 `OPENAI_API_KEY`를 사용하고, Agents SDK가 모델 호출·함수 도구 실행·최종 답변을 소유한다. 키가 없거나 SDK 호출이 timeout/오류가 나면 `offline_demo` 이벤트를 명시한 뒤 deterministic synthetic gym 결과로 데모를 끝까지 이어간다.

최종 결과와 원시 이벤트는 같은 `run_id`로 연결하며, 로컬 JSONL은 `RAW_EVENT_LOG_DIR` (기본 `artifacts/raw-streams`) 아래에 저장한다.

로컬 실행:

```bash
uv run uvicorn agent24.api.app:app --reload
```
