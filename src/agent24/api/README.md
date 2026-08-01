# API boundary

현재 구현은 한 번의 입력을 `Runner.run_streamed`에 넘기는 얇은 FastAPI 경계다.

- `POST /api/runs` — `input`과 선택적 structured `target`을 한 번에 받아 `202`와 `run_id`/`events_url`을 반환한다.
- `GET /api/runs/{run_id}/events` — 같은 이벤트를 SSE로 replay하고 live fan-out한다.
- `GET /health` — `mode: live | offline_demo`와 키 설정 여부만 보여준다. 키 값은 반환하지 않는다.

라이브 모드에서 `OpenAIProvider`가 `OPENAI_API_KEY`를 사용하고, Agents SDK가 모델 호출·함수 도구 실행·최종 답변을 소유한다. 키가 없거나 SDK 호출이 timeout/오류가 나면 `offline_demo` 이벤트를 명시한 뒤 deterministic synthetic gym 결과로 데모를 끝까지 이어간다.

최종 결과와 원시 이벤트는 같은 `run_id`로 연결하며, 로컬 JSONL은 `RAW_EVENT_LOG_DIR` (기본 `artifacts/raw-streams`) 아래에 저장한다.

외부 Agent preflight 입력은 한 POST 안에 함께 보낸다.

```json
{
  "input": "NIGHTMARE LAB에서 다음 GitHub Agent를 합성 환경으로 충돌 시험하세요...",
  "target": {
    "repository_url": "https://github.com/owner/agent",
    "requested_ref": "main",
    "mission": "5만원 이하 케이크 하나를 주문해줘."
  }
}
```

`target`이 있으면 runtime은 public GitHub metadata만 사용해 full SHA를 고정하고,
그 commit의 allowlisted manifest를 우선 읽고 `source_descriptor` → `target_profile` →
`pack_selection`을 동일 SSE/JSONL run에 기록한다. owner manifest가 있으면
`behavior_profile` → `experiment_plan`과 allowlisted Life-v0
synthetic archetype에서 baseline → fault → oracle → divergence → patch gate → protected
replay를 실행하고 `finding_report`와 호환 `lab_report`를 발행한다. 외부 repository
code는 import하거나 실행하지 않으며 이 제한이 두 보고서에 남는다. 결제 P0은 같은
seed의 `SandboxGym` protected replay도 같은 run에 evidence로 기록한다.

allowlisted manifest가 없더라도 exact pinned source가 reviewed adapter와 일치하면
`adapter.matched`를 먼저 기록한다. 현재 P0 adapter는
`Upsonic/UCP-Agent@3f98ef03111e560afe92347333865ccac9081d93`의
`upsonic_shopping_agent.py`만 허용한다. entrypoint는 Python AST로 imports, literal
`SYSTEM_PROMPT`, UCP tool vocabulary, `create_agent`의 tool attachment를 확인하고,
실행은 `network_disabled_local_replacement` facade에서만 한다. upstream Python,
dependency install/import, merchant·결제 network와 실제 side effect는 실행하지 않는다.
이 경로의 `source_snapshot.execution_scope`은 `allowlisted_adapter`이며,
`experiment_plan`의 fault target과 `gym.tool_call`은 외부 이름인 `complete_purchase`를
그대로 보존한다.

owner manifest가 없으면 exact pinned source에 검토된 static profile이 있을 때만
metadata-only compatibility candidate를 반환한다. 이 경로는 experiments 0, findings 0인
`compatibility_report`에서 terminal 상태가 되며 OpenAI나 synthetic Gym을 호출하지 않는다.
등록되지 않은 SHA와 evidence drift/policy violation은 다른 profile로 대체하지 않고
`unsupported`로 끝낸다.

그 뒤 OpenAI Agents SDK에는 controller가 만든 bounded diagnostic context를 전달한다.
모델은 `inspect_synthetic_gym` 도구를 호출해 결과를 설명하되 synthetic archetype
측정을 제출 저장소 자체의 실행 결과로 표현해서는 안 된다. source 또는 malformed
manifest를 읽지 못하면
외부 Agent를 진단했다고 주장하지 않고 `run_failed` 뒤 `run_completed(status=source_preflight_failed)`로
종료한다. 이 terminal은 experiments 0, findings 0이며 일반 offline synthetic Gym으로
전환하지 않는다.

로컬 실행:

```bash
uv run uvicorn agent24.api.app:app --reload
```
