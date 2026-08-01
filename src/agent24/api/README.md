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
그 commit의 allowlisted manifest를 우선 읽어 `source_descriptor`와 bounded
`source_snapshot`을 기록한다. 검토된 local bundle은 이 시점에 계획을 만들지 않고
먼저 대상 Agent를 manifest 도구가 선택한 network-disabled SandboxGym에서 실행한다.
provider가 있으면 `ExampleCakeAgent LLM`이 Agents SDK로 목표를 수행하고, key가 없으면
`reference_source_child` fallback이 같은 observation event contract를 사용한다.
`target.observation.*`에는 실제 tool call/result, ledger mutation, world diff와 초기 oracle가
남고, 이 관찰이 끝난 뒤에만 `BehaviorProfile`, domain Gym/allowlisted candidate,
`experiment_plan`을 구성한다. 이후 baseline → fault → oracle → divergence → patch gate →
protected replay는 `target.execution.*`로 별도 기록한다. 외부 repository code는 일반
경로에서 import하거나 실행하지 않으며 이 제한이 두 보고서에 남는다.

Research/Stock manifest는 같은 observation-first 계약으로 각각 `Research Agent AUT`와
`Stock Analyst AUT`를 host-owned read-only Gym에 연결한다. 대상 Agent에는 manifest/profile이
선택한 domain tool만 주입하고, `target.observation.tool_call`/`tool_result`의 raw SDK item과
structured assessment에서 oracle finding을 만든다. 그 뒤 `DomainExperimentPlan`을 만들고
동일 fixture/seed의 `research_protected_replay()` 또는 `stock_protected_replay()`를
controller-owned verification으로 실행한다. API key가 없을 때는 `reference_fallback`으로
명시하며 LLM target 실행으로 포장하지 않는다. 두 pack 모두 실제 repository code, network
research/market call, trade side effect를 실행하지 않는다.

검토된 self-target인 `caffeine-fighter/AGENT24@<full SHA>`의
`.agent24/manifest.json`이 `agent24.target.v1`과
`src/agent24/agent/target_runtime.py`를 정확히 선언하면, 이 deterministic Lab 경로에
더해 host-owned `Cake Buyer AUT`를 실제 OpenAI Agents SDK `Agent + Runner`로 실행한다.
AUT에는 `SandboxGym.tools()`만 주입하며, `target.tool_call`/`target.tool_result`는
원본 SDK item으로 보존된다. `target.oracle`은 모델의 final answer보다 먼저 target
ledger를 판정하고, `target.replay`는 같은 fixture/seed에서 reconcile 보호 정책의
2-charge → 1-charge 차이를 기록한다. `target.runtime.started`와 `target.oracle`에는
source SHA, manifest/adapter/runtime/prompt hash가 함께 들어간다. target runtime이
timeout·crash·turn limit에 걸리면 synthetic 성공으로 바꾸지 않고 typed terminal로
끝난다.

기본 `local://agent24/examples/demo-agent-repo` bundle은 고정 bundle SHA·manifest hash가
일치하면 `target.observation.*`에서 먼저 대상 Agent를 실행한다. provider가 있으면 Agent
이름은 `ExampleCakeAgent LLM`이고 기본 모델은 `gpt-5.6-luna`이다. manifest가 선언한
`catalog.search/payment.charge/payment.status/calendar.create`만 host-owned SandboxGym으로
dispatch되며, provider 호출은 host boundary에 남는다. 이 단계의
`target.observation.oracle`은 관찰 결과를 표시할 뿐, 아직 planned experiment의 finding으로
확정하지 않는다. 그 다음 controller가 초기 source/manifest/trace를 분석하고 다섯 strict
tool을 통해 allowlisted Gym 실험을 선택한다. 계획된 source 실행은
`target.execution.*`으로 분리된 reference verification이며, key가 없으면 초기 관찰과
controller fallback이 각각 명시적으로 표시된다.

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

키가 설정된 external target에서는 initial observation 이후 OpenAI Agents SDK가 bounded
controller를 구동하며, 실험은 세 번째 tool이 선택한 allowlisted plan에서만 시작된다.
checked-in local bundle에서는 `run_sandbox_experiment`도 같은 source의 bounded child와
stateful SandboxGym을 사용한다. source를 resolve하지 못하거나 malformed manifest를
읽지 못하면 외부 Agent를 진단했다고 주장하지 않고
`stage_failed(stage=source)` 뒤 정확히 하나의
`run_completed(status=source_unresolved/source_preflight_failed)`로 종료한다. 이 terminal은
experiments 0, findings 0이며 일반 offline synthetic Gym으로 전환하지 않는다.

로컬 실행:

```bash
uv run uvicorn agent24.api.app:app --reload
```
