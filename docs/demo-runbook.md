# NIGHTMARE LAB 데모 runbook

결선 7분은 `슬라이드 2분 + live demo 3분 + Surprise Task 2분`으로 고정한다.
repository/ref/mission을 한 번 제출한 뒤에는 어떤 단계 버튼도 누르지 않는다.

> **항상 먼저 말할 경계:** “이 실행은 고정된 source contract와 network-disabled local
> replacement를 쓰는 모의 실험입니다. 제출 Python과 실제 결제·메일·일정 변경은 없습니다.”

## 현재 작동 데모 계약 · 공개 GitHub UCP adapter

현재 실제 GitHub 입력을 받는 주 경로는 아래의 공개 UCP Agent이다. local FastAPI
(`scripts/demo-local.py --live-github`)와 Sites hosted API가 같은 source/ref/mission을
받아 각각 bounded source contract를 확인하고, `ucp-shopping-v0` adapter의
network-disabled local replacement Gym만 실행한다.

```text
Repository: https://github.com/Upsonic/UCP-Agent
Ref: 3f98ef03111e560afe92347333865ccac9081d93
Mission: 구매 완료 뒤 응답이 끊겨도 중복 결제가 없는지 안전하게 검증해 주세요.
```

성공 run은 다음 evidence를 모두 보여야 한다.

- GitHub ref가 위 40자 SHA로 resolve되고 `upsonic_shopping_agent.py` 한 파일만
  bounded download된다. 현재 rehearsal source snapshot은 2,875 bytes다.
- local 경로는 source를 AST로, hosted 경로는 bounded static contract로 확인하고
  `adapter.matched`에 관찰된 UCP tool 목록·source hash·검사 방법을 남긴다.
- 선택된 fault는 `commit_then_timeout` on `complete_purchase`다.
- `CLONE → CRASH → AUTOPSY → VACCINE → REPLAY`가 모두 Raw Stream에 남는다.
- 보호 전: complete purchase 2건, 주문 2개, 총 ₩98,000, 합성 지갑 ₩402,000이다.
- controller oracle이 exactly-once, purchase count, total spend 위반을 측정한다.
- 보호 후: complete purchase 1건, 주문 1개, 총 ₩49,000, 합성 지갑 ₩451,000이다.
- same-seed·neighbor·benign gate가 통과하고 `protected_replay.accepted=true`다.
- report는 `verified_mitigation`을 사용하되, stale order-status와 upstream runtime은
  residual risk로 남긴다. OpenAI key가 없으면 모델 주도 tool loop를 실행하지 않고
  `stage_failed(openai_key_missing)` 뒤 같은 target의 deterministic reference plan을
  명시적으로 실행한다. terminal은 `status=openai_analysis_unavailable`,
  `openai_analysis_completed=false`다.
- 현재 관찰 event 수는 local API 38개, hosted Sites 41개다. 이는 현재 rehearsal의
  계수이며 UI/API contract가 모든 실행에서 같은 고정 개수를 약속하는 것은 아니다.

항상 먼저 말할 경계는 다음과 같다. “고정된 source contract를 정적으로 확인하고
network-disabled local replacement를 실행한 모의 실험입니다. 제출 Python·upstream
dependency와 실제 merchant·결제 side effect는 실행하지 않았습니다.”

ref가 틀리거나 source preflight가 실패하면 `stage_failed(stage=source)`와
`run_completed(status=source_unresolved/source_preflight_failed)`만 남기고 Gym을
실행하지 않는다. 이 경로를 성공 결과로 포장하지 않는다.

## 보조 호환 계약 · owner manifest

```text
Repository: https://github.com/caffeine-fighter/AGENT24
Ref: main
Mission: 엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘.
```

성공 run은 다음 evidence를 모두 보여야 한다.

- ref가 40자 full commit SHA로 resolve된다.
- `source_descriptor → behavior_profile → experiment_plan`이 Raw Stream에 남는다.
- 선택된 fault는 `commit_then_timeout` on `payment.charge`다.
- 보호 전: charge 2건, 주문 2개, 총 ₩98,000, 합성 지갑 ₩402,000이다.
- controller oracle이 exactly-once, purchase count, total spend 위반을 측정한다.
- 보호 후: charge 1건, 주문 1개, 총 ₩49,000, 합성 지갑 ₩451,000이다.
- `budget`, `count`, `task`, `benign` 네 check가 PASS다.
- same-seed 재현은 `3/3`, protected replay는 `accepted=true`다.
- blanket payment block은 charge 0건이어도 mission 실패로 REJECT된다.
- terminal은 네 stage truth 필드를 포함한다. key가 있으면 OpenAI model이 다섯 bounded
  tool을 순서대로 호출한 뒤 controller가 final을 검증한다. key/provider가 unavailable이면
  `planner.comparison(fallback_policy=same_target_reference)`를 먼저 남기고 같은 target의
  reference plan이 evidence를 만든다. 이를 unrelated fixture나 live 성공으로 표시하지 않는다.

아래 owner manifest 계약의 33개 event는 2026-08-01의 offline 설명 rehearsal에서 측정한 값이다. live OpenAI
설명은 SDK tool turn에 따라 event가 더 생길 수 있으므로 발표에서는 “최소 event 수”로
일반화하지 않고 해당 rehearsal의 관찰값이라고 말한다.

## 발표 전 준비

- [ ] `git rev-parse HEAD`와 발표할 source/ref를 기록했다.
- [ ] `OPENAI_API_KEY`와 quota 유무만 확인했고 key 값을 화면·log에 노출하지 않았다.
- [ ] `/health`의 `build_commit`과 실제 배포 commit이 같으며 submitted-source SHA와 혼동하지 않는다.
- [x] D1 P0 known-good target은 공개 GitHub의 고정 UCP adapter이며, bounded entrypoint
  contract와 network-disabled local replacement를 확인했다. owner manifest 경로는
  보조 호환 경로로 유지한다.
- [ ] `scripts/hosted-smoke.py`가 production에서 `run_mode=openai_hosted`와 `openai_response_observed=true`로 통과했다.
- [ ] private repository라면 API 서버가 읽는 `GITHUB_TOKEN` 권한을 확인했다.
- [ ] `scripts/external-smoke.py`용 `GITHUB_TOKEN`도 process environment에 주입했다.
- [ ] known-good external smoke가 3회 연속 동일 결과를 냈다.
- [ ] Surprise fixed matrix가 `5/5 PASS`다.
- [x] 브라우저에서 정상·지원하지 않는 입력·API 연결 실패 세 경로를 확인했다.
- [ ] browser zoom과 font size에서 world cards와 Raw Stream이 동시에 보인다.
- [ ] OS notification, 개인 bookmark, terminal history, `.env`가 녹화 화면에 없다.
- [ ] replay/reset 버튼을 누르지 않고 한 번의 submit으로 끝까지 완주했다.
- [ ] 제출 영상과 발표 slide의 모든 수치가 현재 rehearsal 값과 일치한다.
- [ ] fallback 영상 또는 screenshot은 `AUTO / FIXTURE` 표시를 자르지 않았다.

## 실행 명령

### Windows / PowerShell

```powershell
uv sync --extra dev
.\scripts\demo.ps1 -OpenBrowser
```

### macOS / Linux

```bash
uv sync --extra dev
uv run uvicorn agent24.api.app:app --host 127.0.0.1 --port 8000
```

브라우저에서 <http://127.0.0.1:8000>을 연다. API key가 없으면 시작 전에 상단 상태 배너가
실제 OpenAI 모델 진단을 실행하지 않는다고 표시된다. structured target 실행 뒤에는
`stage_failed(openai_key_missing)`와 `planner.comparison(same_target_reference)`가 남고,
동일 target reference 결과를 live OpenAI 결과로 설명하지 않는다. `offline_demo` tool event는
target 없는 generic 입력에만 허용된다.

별도 원격 저장소나 배포 없이, 체크인된 예시 Agent로 전체 경로를 리허설할 때는 다음
명령을 사용한다.

```bash
uv run python scripts/demo-local.py
```

이 명령은 `examples/demo-agent-repo`의 manifest와 entrypoint bytes를 64자 bundle SHA-256으로
고정한다. 정확히 검토된 이 bundle과 고정 mission에 한해서 entrypoint를 `python -I -S`
child에서 실행하며, 모든 tool call은 network-disabled host-owned SandboxGym으로 전달한다.
임의 로컬 경로와 외부 GitHub source는 실행하지 않고 실제 서비스 side effect도 만들지 않는다.

예시 participant Agent repo로 self-contained 데모를 할 때는 다음 명령을 사용한다.

```bash
uv run python scripts/demo-local.py --example-agent --port 8769
```

그리고 <http://127.0.0.1:8769/index.html?demo=example-agent>를 연다. 이 경로는
`local://agent24/examples/demo-agent-repo`를 `local_bundle` source kind와
`local-bundle` resolver로 bounded intake한다. allowlisted path와 bytes에서 계산한 64자
bundle SHA-256 revision, manifest/entrypoint의 Git blob SHA 및 SHA-256 content hash를
보존하며 이를 Git commit으로 표시하지 않는다. 별도 공개 GitHub 저장소나 fake 좌표를
사용하지 않는다. example-agent 모드의 고정 mission은 “케이크 1개 주문 + 가족 캘린더
등록”이며, exact hash를 통과한 entrypoint만 child runner가 실행한다. 다른 mission·bundle
bytes·path는 child 생성 전에 거부한다.

실제 공개 GitHub 입력을 브라우저에서 검증하려면 다음 모드로 실행한다.

```bash
uv run python scripts/demo-local.py --live-github --port 8769
```

이 모드는 UI가 입력한 repository/ref를 GitHub metadata로 full SHA에 고정하고, bounded
manifest/entrypoint 또는 exact-reviewed `ucp-shopping-v0` adapter를 선택한다. adapter
경로는 `ALLOWLISTED ADAPTER`, `complete_purchase`, `network disabled`와 원본 Raw Stream을
화면에 남기며 submitted Python이나 upstream dependency를 실행하지 않는다.

### OpenAI bounded controller 검증

기본 test gate는 실제 quota를 사용하지 않고 official Agents SDK/Responses provider 경계만
mocking한다. 다섯 strict tool의 정확한 순서, raw call/result, SSE=JSONL, 조기 final 거부,
same-target reference fallback과 secret 비노출을 검사한다.

```bash
uv run pytest -q tests/unit/test_diagnostic_controller.py \
  tests/integration/test_api.py::test_live_target_passes_bounded_synthetic_evidence_to_openai \
  tests/integration/test_api.py::test_live_target_rejects_a_premature_model_final_then_runs_named_reference
```

실제 provider smoke는 quota를 실수로 쓰지 않도록 이중 opt-in이다. `RuntimeSettings`가
server process의 `OPENAI_API_KEY` 또는 untracked `.env`만 읽으며, request body·browser·AUT
child에는 key를 전달하지 않는다. Raw JSONL은 pytest의 임시 디렉터리에만 생성되고 종료 후
삭제된다. key 값, request body, Raw Stream을 출력하거나 commit하지 않는다.

```bash
AGENT24_RUN_REAL_OPENAI_SMOKE=1 \
AGENT24_REAL_OPENAI_MODEL=gpt-5.4-mini \
uv run pytest -q \
  tests/integration/test_openai_diagnostic_smoke.py
```

2026-08-02 local release 측정은 **3/3 PASS**, 총 **30.11초**(평균 약 10.0초)였다.
서로 다른 run ID 세 개가 동일한 local bundle source ref, initial snapshot, evidence ID,
vulnerable/protected trace digest와 각 3회 replay digest를 만들었다. 매 run은 다음 계약을
검증했다.

- `/health`: `status=ok`, `mode=live`, `openai_configured=true`; secret 비노출
- raw model tools: `inspect_target → list_experiments → run_sandbox_experiment →
  inspect_evidence → verify_mitigation` 정확히 5개
- local AUT trace: `target.execution.plan/started/tool_call/tool_result/completed`
- controller evidence: 취약 charge 2건, 보호 후 1건, `sandbox.evidence`, `oracle.report`
- replay/final: `protected_replay.accepted=true`, OpenAI `final_output`, 마지막
  `run_completed(status=completed, mode=live, execution_scope=target_sandbox)`
- SSE와 임시 JSONL의 parsed event sequence 동일, configured key 문자열 0건

실제 provider가 primitive 비용을 추정하지 않도록 v5 strict schema는 모든 tool의
`budget=64`를 강제한다. 실제 사용량은 controller가 `budget_spent`로 측정하며 64를 넘는
결과는 거부한다.

현재 `run_sandbox_experiment`는 exact local bundle에서 #100 bounded child runner를 주
실행 primitive로 사용한다. baseline·fault·3회 replay·protected replay·neighbor·benign·
blanket-block·minimized run은 같은 source SHA, fixture, seed, 초기 snapshot에 묶이며
`sandbox.evidence`의 raw trace/ledger와 같은 evidence ID가 report와 protected replay에
연결된다. runner crash·budget stop은 finding 없이 typed terminal로 끝난다.

실패 terminal은 다음 순서를 사용하며 어느 것도 live 성공으로 표시하지 않는다.

| 조건 | terminal | 증거 정책 |
|---|---|---|
| 실제 provider 성공 | `completed` | 5-tool OpenAI final + target sandbox evidence |
| key 없음 | `openai_analysis_unavailable` | 같은 target deterministic reference, OpenAI 완료=false |
| source 미지원/미해결 | `source_unresolved` | experiment·finding 없음 |
| sandbox crash/budget stop | `diagnostic_loop_failed` | finding·report·replay 없음 |
| provider timeout | `openai_analysis_failed` | typed failure 뒤 같은 target reference만 실행 |

브라우저 canonical gate는 같은 form → SSE → 단일 terminal 시나리오를 desktop Chromium과
Pixel 7 mobile Chromium에서 각각 실행한다.

```bash
cd site
npm run test:browser
```

2026-08-02 측정은 **6/6 PASS**였다. 이 검증은 로컬 서버/브라우저 경로이며 별도 공개
repository 생성이나 deployment를 요구하지 않는다.

### 3회 known-good 사전 검증

`external-smoke.py`는 `.env`를 직접 읽지 않고 process의 `GITHUB_TOKEN`만 사용한다.
private repository에서 token을 주입하지 않으면 GitHub가 404를 반환하는 것이 정상이다.
token 값은 출력하거나 파일에 쓰지 않는다.

```powershell
uv run python scripts/external-smoke.py --runs 3
```

성공 출력에는 다음 값이 있어야 한다.

```text
runs=3
stable=true
finding_status=verified_mitigation
reproduction=3/3 same-seed replays
sandbox_accepted=true
```

`source_ref`, `manifest_hash`, `plan_id`, `seed`도 세 run에서 같아야 한다. 이는
allowlist manifest로 선택한 synthetic archetype의 측정이며 repository code 실행
결과가 아니다.

### Surprise 사전 검증

API 서버를 켠 상태에서 실행한다.

```powershell
uv run python scripts/surprise-smoke.py --base-url http://127.0.0.1:8000
```

`passed=true`, `summary.passed=5`, `summary.total=5`, `duplicate_run_ids=false`가 아니면
발표에 들어가지 않는다. raw run output은 commit하지 않는다.

## 0:00–2:00 · 슬라이드 5장

| timecode | slide | 행동·핵심 문장 |
|---|---|---|
| `0:00–0:20` | 1 | 첫 10초 안에 “1개 요청 → 합성 세계 2개·₩98,000”을 보여주고 실제 결제가 없다고 말한다. |
| `0:20–0:42` | 2 | side-effect Agent 개발팀이라는 타깃과 정상 예시만으로 못 보는 failure gap을 설명한다. |
| `0:42–1:10` | 3 | 한 입력과 `CLONE → CRASH → AUTOPSY → VACCINE → REPLAY`, controller oracle, Raw Stream을 설명한다. |
| `1:10–1:40` | 4 | 2→1 charge, ₩98,000→₩49,000, 3/3, 33 events, benign PASS, block-all REJECT를 보여준다. |
| `1:40–2:00` | 5 | 돈·커뮤니케이션·시간·데이터 Surprise 선택을 요청하고 live 화면으로 전환한다. |

말할 원고와 화면 copy는 [`../slides/deck.md`](../slides/deck.md)를 그대로 사용한다.

## 2:00–5:00 · live demo 3분

| live 경과 | 화면 행동 | 발표자 cue |
|---|---|---|
| `0:00–0:20` | form의 repository/ref/mission과 상단 simulation badge를 보여준다. | “세 값을 한 번 제출하고 이후에는 손을 떼겠습니다.” |
| `0:20–0:40` | `악몽 시작`을 한 번 누른 뒤 cursor를 치운다. resolved SHA와 `CLONE`을 가리킨다. | “source를 immutable commit으로 고정하고 manifest와 선언된 entrypoint를 bounded evidence로 읽습니다.” |
| `0:40–1:20` | BehaviorProfile 다섯 assessment, selected fault의 `WHY`와 `EXPECT`, Raw Stream을 보여준다. | “성격을 추측하지 않고 evidence 또는 unknown으로 기록합니다.” |
| `1:20–2:05` | charge 2건, timeout unknown, 2개·₩98,000, 세 invariant와 first divergence를 보여준다. | “관찰 사실과 원인 가설을 분리합니다.” |
| `2:05–2:35` | antibody policy와 `PROPOSED`를 보여준다. | “전부 막지 않고 idempotency와 reconciliation만 추가합니다.” |
| `2:35–2:55` | protected world 1개·₩49,000, 네 PASS, blanket-block rejection을 보여준다. | “기능을 보존했을 때만 verified입니다.” |
| `2:55–3:00` | Raw Stream과 residual scope에서 멈춘다. | “실제 provider와 stale status는 아직 미검증입니다.” |

run이 3분보다 빨리 끝나도 replay 버튼으로 시간을 늘리지 않는다. 완료된 event를 위
순서로 되짚는다. OpenAI final explanation을 기다리느라 controller evidence를 건너뛰지
않는다.

## 5:00–7:00 · Surprise Task 2분

| 경과 | 행동 | 발표자 cue |
|---|---|---|
| `0:00–0:15` | 돈·커뮤니케이션·시간·데이터 중 하나를 고르게 한다. | “어떤 악몽을 고르시겠어요?” |
| `0:15–0:30` | [`surprise-missions.md`](surprise-missions.md)의 exact mission을 한 번 제출한다. | “같은 POST와 SSE 경로입니다. 이제 손을 떼겠습니다.” |
| `0:30–1:15` | Raw `tool_call`, `tool_result`, scenario, `external_side_effects=false`를 짚는다. | “고정 runner가 evidence로 판정하며 LLM은 judge가 아닙니다.” |
| `1:15–1:40` | terminal과 failure signal/probe를 보여준다. | “이 작은 synthetic scenario에서 관찰한 신호입니다.” |
| `1:40–2:00` | scope badge와 safety disclaimer를 보여준다. | “외부 코드는 실행하지 않았고 실패 미발견은 안전 인증이 아닙니다.” |

## Fallback ladder

발표자는 실패를 숨기거나 live처럼 연기하지 않고 아래 첫 번째 가능한 경로를 사용한다.

1. **OpenAI key/quota/timeout 실패:** target run은 `stage_failed(stage=openai_analysis)`와
   `planner.comparison(fallback_policy=same_target_reference)`를 그대로 보여준다. 같은 target의
   deterministic reference plan만 실행하며 generic fixture tool event를 추가하거나 모델
   분석으로 포장하지 않는다. target 없는 generic 실행만 `offline_demo` fixture를 사용할 수 있다.
2. **source/manifest preflight 실패:** “외부 Agent 진단을 완료하지 못했다”고 말하고
   `source_preflight_failed`를 보여준다. 이 상태를 known-good 결과로 포장하지 않는다.
3. **API 또는 첫 SSE 기록 실패:** local은 1.6초, hosted는 22초 뒤 브라우저가 자동 실행하는
   내장 예시를 사용한다. 돈 입력은 `life.payment_intent_timeout.v1`을 보여주되 “API에
   연결하지 못해 내장 예시를 보여드려요” 안내를 유지하고, 제출한 저장소 분석 결과라고
   말하지 않는다. 지원하지 않는 Surprise 입력은 결제 예시로 바꾸지 않고 끝낸다.
4. **브라우저 렌더링 실패:** 같은 rehearsal에서 촬영한 120초 제출 영상을 재생하고
   slide 4의 actual measurement로 설명한다. 영상도 synthetic badge를 포함해야 한다.
5. **Surprise scenario mismatch:** PASS라고 말하지 않는다. 오류를 그대로 보여주고
   known-good 케이크 scenario로 제품 원리와 미지원 범위를 설명한다.

## 2026-08-01 KST 리허설 기록

기준 checkout은 `origin/main@69f7f2b`, Python 3.12/`uv`, macOS였다. raw run log와
run id는 repository에 남기지 않고 아래 aggregate evidence만 기록했다.

| 항목 | 실행 결과 | 판정 |
|---|---|---|
| private repository smoke, token 미주입 | GitHub API 404 → `SourceAccessError` | 예상된 `source_preflight_failed` terminal과 token 안내를 검증함 |
| token을 process header 경로로 주입한 `external-smoke.py --runs 3` | full SHA `69f7f2bcb18716e5c1f06b4d31a7e90a7dfff90d`, manifest SHA-256 `4ea67bec1bf484b6e965595ce22130b26fbb18745b8a8ceedbbce5d12838fe1b`, `stable=true`, `3/3`, `accepted=true` | PASS |
| full structured API, OpenAI key 없음 | 모델 loop 미실행, `stage_failed(openai_key_missing)`와 `planner.comparison(same_target_reference)` 뒤 같은 target reference evidence·네 check 보존, terminal `openai_analysis_unavailable`, generic fixture event 없음 | PASS |
| source token 없음 | `run_started → phase.changed → stage_failed(source_preflight_failed) → run_completed(experiments=0)` | PASS |
| repository 고정 Surprise matrix | 5/5, 각 6 events, expected scenario 일치, 고유 run id, `external_side_effects=false` | PASS |
| 돈·커뮤니케이션·시간·데이터·bonus exact mission | 5/5, 각 6 events, expected scenario 일치, `external_side_effects=false` | PASS |
| deterministic web fixture reducer | `node web/tests/core.test.mjs` 통과 | PASS |
| 실제 브라우저, API 연결 실패 | 제출 1회 뒤 “내장 예시” 안내; 30 events; 2건·₩98,000 → 1건·₩49,000; 마지막 기록 1개 | PASS |
| 실제 브라우저, 지원하지 않는 입력 | 폼 → POST → SSE; 결제 흔적과 `experiment_plan` 없음; `unsupported` 마지막 기록 1개 | PASS |

### 3회 연속 full-path 리허설 gate

아래 표는 2026-08-01의 이전 offline-fallback 계약으로 실행한 역사적 기록이다. #98의
no-silent-fallback 계약 이후 release gate 증거로 재사용하지 않으며, 동일 source/ref/mission의
3회 live smoke는 #103에서 다시 측정한다. 시간은 HTTP 제출부터 terminal SSE 수신까지의
당시 wall time이며 사람의 7분 발표 rehearsal 시간과 혼동하지 않는다.

| 회차 | source SHA | 2→1 / ₩98,000→₩49,000 | Raw Stream | fallback 전환 | 7분 이내 |
|---|---|---|---|---|---|
| 1 | `69f7f2bcb18716e5c1f06b4d31a7e90a7dfff90d` | PASS | 33 events / contiguous PASS | `offline_demo` PASS | 0.920초 PASS |
| 2 | `69f7f2bcb18716e5c1f06b4d31a7e90a7dfff90d` | PASS | 33 events / contiguous PASS | `offline_demo` PASS | 1.287초 PASS |
| 3 | `69f7f2bcb18716e5c1f06b4d31a7e90a7dfff90d` | PASS | 33 events / contiguous PASS | `offline_demo` PASS | 0.819초 PASS |

최종 PDF·영상과 발표자의 실제 7분 delivery는 별도 제출 gate다. 이 표는 이전 구현 경로의
역사적 기록일 뿐 현재 계약의 완주를 증명하거나 사람의 발표 rehearsal을 대신하지 않는다.

제품 문구와 수치는 [`cake-collision-product-contract.md`](cake-collision-product-contract.md),
입력·오류 상태는 [`external-agent-contract.md`](external-agent-contract.md), 심사 답변은
[`judging-checklist.md`](judging-checklist.md)를 기준으로 한다.
