# API boundary

> Implementation snapshot: the ratified D3 replacement contract is
> [`web/event-contract.md`](../../../web/event-contract.md). The required legacy
> `input`, optional `target`, `{detail}` errors, and multi-terminal fallback described
> below are migration drift until A4 lands; they are not alternate product choices.

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
그 commit의 allowlisted manifest만 읽어 `source_descriptor` → `behavior_profile` →
`experiment_plan`을 동일 SSE/JSONL run에 기록한다. 이어서 allowlisted Life-v0
synthetic archetype에서 baseline → fault → oracle → divergence → patch gate → protected
replay를 실행하고 `finding_report`와 호환 `lab_report`를 발행한다. 외부 repository
code는 import하거나 실행하지 않으며 이 제한이 두 보고서에 남는다. 결제 P0은 같은
seed의 `SandboxGym` protected replay도 같은 run에 evidence로 기록한다.

그 뒤 OpenAI Agents SDK에는 controller가 만든 bounded diagnostic context를 전달한다.
모델은 `inspect_synthetic_gym` 도구를 호출해 결과를 설명하되 synthetic archetype
측정을 제출 저장소 자체의 실행 결과로 표현해서는 안 된다. source/manifest를 읽지 못하면
외부 Agent를 진단했다고 주장하지 않고 `run_failed` 뒤 명시적 `offline_demo`로 전환한다.

로컬 실행:

```bash
uv run uvicorn agent24.api.app:app --reload
```
