# NIGHTMARE LAB 데모 runbook

결선 7분은 `슬라이드 2분 + live demo 3분 + Surprise Task 2분`으로 고정한다.
repository/ref/mission을 한 번 제출한 뒤에는 어떤 단계 버튼도 누르지 않는다.

> **항상 먼저 말할 경계:** “이 실행은 synthetic archetype 측정입니다. 외부 저장소
> 코드는 실행하지 않았고 실제 결제·메일·일정 변경도 없습니다.”

## Known-good 계약

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
- terminal은 `live` 또는 명시적 `offline_demo`다. offline이어도 위 controller
  evidence가 먼저 완주해야 한다.

33개 event는 2026-08-01의 offline 설명 rehearsal에서 측정한 값이다. live OpenAI
설명은 SDK tool turn에 따라 event가 더 생길 수 있으므로 발표에서는 “최소 event 수”로
일반화하지 않고 해당 rehearsal의 관찰값이라고 말한다.

## 발표 전 준비

- [ ] `git rev-parse HEAD`와 발표할 source/ref를 기록했다.
- [ ] `OPENAI_API_KEY`와 quota 유무만 확인했고 key 값을 화면·log에 노출하지 않았다.
- [ ] `/health`의 `build_commit`과 실제 배포 commit이 같으며 submitted-source SHA와 혼동하지 않는다.
- [ ] D1 P0 known-good target은 공개 GitHub이고 allowlist manifest를 제공한다.
- [ ] `scripts/hosted-smoke.py`가 production에서 `run_mode=openai_hosted`와 `openai_response_observed=true`로 통과했다.
- [ ] private repository라면 API 서버가 읽는 `GITHUB_TOKEN` 권한을 확인했다.
- [ ] `scripts/external-smoke.py`용 `GITHUB_TOKEN`도 process environment에 주입했다.
- [ ] known-good external smoke가 3회 연속 동일 결과를 냈다.
- [ ] Surprise fixed matrix가 `5/5 PASS`다.
- [x] API unavailable → browser fixture fallback을 실제 브라우저에서 확인했다.
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

브라우저에서 <http://127.0.0.1:8000>을 연다. API key가 없으면 화면과 stream에
`offline_demo`가 표시되며 deterministic controller 경로는 계속 실행된다.

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
| `0:20–0:40` | `악몽 시작`을 한 번 누른 뒤 cursor를 치운다. resolved SHA와 `CLONE`을 가리킨다. | “source를 immutable commit으로 고정하고 manifest만 읽습니다.” |
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

1. **OpenAI key/quota/timeout 실패:** `run_failed` 또는 `offline_demo` badge를 그대로
   보여준다. controller가 이미 만든 Gym evidence, report, Raw Stream을 설명한다.
2. **source/manifest preflight 실패:** “외부 Agent 진단을 완료하지 못했다”고 말하고
   `source_preflight_failed`를 보여준다. 이 상태를 known-good 결과로 포장하지 않는다.
3. **API 또는 첫 SSE event 실패:** local은 1.6초, hosted는 22초 뒤 브라우저가 자동 실행하는
   `life.payment_intent_timeout.v1` fixture를 사용한다. `FIXTURE FALLBACK · 제출 target 분석 결과가 아님`과
   `SYNTHETIC ARCHETYPE` 경계를 자르지 않는다.
4. **브라우저 렌더링 실패:** 같은 rehearsal에서 촬영한 120초 제출 영상을 재생하고
   slide 4의 actual measurement로 설명한다. 영상도 synthetic badge를 포함해야 한다.
5. **Surprise scenario mismatch:** PASS라고 말하지 않는다. 오류를 그대로 보여주고
   known-good 케이크 scenario로 제품 원리와 미지원 범위를 설명한다.

## 2026-08-01 KST 리허설 기록

기준 checkout은 `origin/main@69f7f2b`, Python 3.12/`uv`, macOS였다. raw run log와
run id는 repository에 남기지 않고 아래 aggregate evidence만 기록했다.

| 항목 | 실행 결과 | 판정 |
|---|---|---|
| private repository smoke, token 미주입 | GitHub API 404 → `SourceAccessError` | 예상된 preflight failure. token/fallback 설명을 검증함 |
| token을 process header 경로로 주입한 `external-smoke.py --runs 3` | full SHA `69f7f2bcb18716e5c1f06b4d31a7e90a7dfff90d`, manifest SHA-256 `4ea67bec1bf484b6e965595ce22130b26fbb18745b8a8ceedbbce5d12838fe1b`, `stable=true`, `3/3`, `accepted=true` | PASS |
| full structured API, OpenAI key 없음 | 33 events, contiguous `seq=0..32`, 2건·₩98,000 → 1건·₩49,000, 네 check PASS, terminal `offline_demo` | PASS |
| source token 없음 fallback | `run_started → phase.changed → run_failed(source_preflight_failed) → offline_demo → tool_call → tool_result → final_output → run_completed` | PASS |
| repository 고정 Surprise matrix | 5/5, 각 6 events, expected scenario 일치, 고유 run id, `external_side_effects=false` | PASS |
| 돈·커뮤니케이션·시간·데이터·bonus exact mission | 5/5, 각 6 events, expected scenario 일치, `external_side_effects=false` | PASS |
| deterministic web fixture reducer | `node web/tests/core.test.mjs` 통과 | PASS |
| 실제 browser, API unavailable | submit 1회 뒤 `AUTO / FIXTURE`, `FIXTURE · source 미조회`, `SYNTHETIC WORLD ONLY`; 30 events; 2건·₩98,000 → 1건·₩49,000; 네 check PASS | PASS |

### 3회 연속 full-path 리허설 gate

동일 source/ref/mission을 full structured API로 세 번 연속 실행했다. 각 회차는 source
preflight, 33-event Raw Stream, controller 진단, protected replay와 명시적 OpenAI
offline fallback을 모두 포함한다. 시간은 HTTP 제출부터 terminal SSE 수신까지의 실제
wall time이며 사람의 7분 발표 rehearsal 시간과 혼동하지 않는다.

| 회차 | source SHA | 2→1 / ₩98,000→₩49,000 | Raw Stream | fallback 전환 | 7분 이내 |
|---|---|---|---|---|---|
| 1 | `69f7f2bcb18716e5c1f06b4d31a7e90a7dfff90d` | PASS | 33 events / contiguous PASS | `offline_demo` PASS | 0.920초 PASS |
| 2 | `69f7f2bcb18716e5c1f06b4d31a7e90a7dfff90d` | PASS | 33 events / contiguous PASS | `offline_demo` PASS | 1.287초 PASS |
| 3 | `69f7f2bcb18716e5c1f06b4d31a7e90a7dfff90d` | PASS | 33 events / contiguous PASS | `offline_demo` PASS | 0.819초 PASS |

최종 PDF·영상과 발표자의 실제 7분 delivery는 별도 제출 gate다. 이 표는 구현 경로의
3회 연속 완주를 증명하며 사람의 발표 rehearsal을 대신했다고 주장하지 않는다.

제품 문구와 수치는 [`cake-collision-product-contract.md`](cake-collision-product-contract.md),
입력·오류 상태는 [`external-agent-contract.md`](external-agent-contract.md), 심사 답변은
[`judging-checklist.md`](judging-checklist.md)를 기준으로 한다.
