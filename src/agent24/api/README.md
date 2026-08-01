# API boundary

현재 구현은 한 번의 입력을 `Runner.run_streamed`에 넘기는 얇은 FastAPI 경계다.

- `POST /api/runs` — `input`과 선택적 structured `target`을 한 번에 받아 `202`와 `run_id`/`events_url`을 반환한다.
- `GET /api/runs/{run_id}/events` — 같은 이벤트를 SSE로 replay하고 live fan-out한다.
- `GET /health` — `mode: live | offline_demo`와 키 설정 여부만 보여준다. 키 값은 반환하지 않는다. UI는 이 값을 바탕으로 실행 전 `OPENAI_API_KEY 없음` 또는 live 경로 사용 가능 상태를 표시한다.

라이브 모드에서 `OpenAIProvider`가 서버의 `OPENAI_API_KEY`를 사용하고, Agents SDK가
모델 turn·함수 도구 dispatch·구조화 final을 소유한다. External target에서는 모델이
`inspect_target → list_experiments → run_sandbox_experiment → inspect_evidence →
verify_mitigation`의 다섯 strict tool을 호출한다. 모델은 controller가 반환한 candidate와
evidence/mitigation ID만 사용할 수 있으며, 순서·budget·oracle bypass·증거 없는 final은
typed rejection으로 닫힌다. SDK의 raw `tool_call`/`tool_result`는 수정 없이 SSE/JSONL에
남는다.

키 부재·timeout·provider/controller 실패 때 external target을 관련 없는 fixture로 바꾸지
않는다. `stage_failed(openai_*)`와 `planner.comparison(fallback_policy=
same_target_reference)`를 먼저 기록한 뒤, 같은 target의 deterministic reference plan을
명시적으로 실행하고 `openai_analysis_completed=false`로 끝낸다. target이 없는 generic
실행만 기존 `offline_demo` synthetic fixture를 사용할 수 있다.

`run_completed`는 유일한 terminal이다. 모든 terminal payload는 `source_resolved`,
`diagnostic_completed`, `openai_analysis_completed`, `execution_scope`를 포함한다.
`stage_failed`와 legacy `run_failed`는 non-terminal이며 credential이나 provider 예외 원문을
포함하지 않는다.

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

키가 설정된 external target에서는 OpenAI Agents SDK가 bounded controller를 먼저
구동하며, 실험은 세 번째 tool이 선택한 allowlisted plan에서만 시작된다. 현재 #101의
`run_sandbox_experiment`는 `DeterministicLabLoop`가 묶어 제공하는 synthetic archetype/
allowlisted replacement primitive다. checked-in participant entrypoint를 #100 child runner로
실행해 주 증거로 연결하는 작업은 #102 범위이며, 완료된 것처럼 주장하지 않는다. source를 resolve하지 못하거나
malformed manifest를 읽지 못하면 외부 Agent를 진단했다고 주장하지 않고
`stage_failed(stage=source)` 뒤 정확히 하나의
`run_completed(status=source_unresolved/source_preflight_failed)`로 종료한다. 이 terminal은
experiments 0, findings 0이며 일반 offline synthetic Gym으로 전환하지 않는다.

로컬 실행:

```bash
uv run uvicorn agent24.api.app:app --reload
```
