# 심사 체크리스트와 Q&A

발표자는 답변을 “주장 → 관찰 evidence → 한계” 순서로 20초 안에 말한다. 실제로
검증하지 않은 repository code, provider, framework까지 범위를 넓히지 않는다.

## 최종 go / no-go

### Pipeline Architecture · 25%

- [x] 입력은 repository/ref/mission 한 번이며 controller가 이후 단계를 소유한다.
- [x] `CLONE → CRASH → AUTOPSY → VACCINE → REPLAY`가 하나의 run으로 이어진다.
- [x] source SHA, BehaviorProfile evidence, plan의 `WHY`·`EXPECT`, oracle, report가 같은 Raw Stream에 남는다.
- [x] raw `tool_call` / `tool_result` payload는 표시용 summary와 분리되어 보존된다.
- [x] source/manifest, diagnostic loop, OpenAI timeout/API 오류에 explicit fallback이 있다.
- [x] 외부 repository code를 실행하지 않는 P0 경계가 UI와 report에 표시된다.

### Real-time Adaptability · 25%

- [x] fixed Surprise matrix 5개가 같은 HTTP/SSE path에서 `5/5 PASS`했다.
- [x] 돈·커뮤니케이션·시간·데이터·bonus exact mission도 같은 path에서 `5/5 PASS`했다.
- [x] 각 Surprise run은 mission 보존, expected scenario, `external_side_effects=false`를 raw evidence로 판정한다.
- [x] OpenAI key가 없는 `offline_demo`와 private source token이 없는 preflight fallback을 실제 실행했다.
- [x] 실제 브라우저에서 정상·지원하지 않는 입력·API 연결 실패 세 경로가 마지막 기록을 정확히 한 번만 남기는지 확인했다.

### Prompt Quality · 20%

- [x] runtime instruction은 synthetic scope, observed/hypothesis 분리, 도구 우선 호출, 실제 action 주장 금지를 명시한다.
- [x] experiment 선택은 LLM keyword guess가 아니라 BehaviorProfile field와 evidence를 읽는 결정적 policy다.
- [x] `unsupported`, `budget_exhausted`, `no_failure_observed`가 취약점이나 안전 인증으로 바뀌지 않는다.
- [x] LLM은 controller evidence를 설명할 뿐 oracle verdict를 생성하지 않는다.

### Impact / Idea · 20%

- [x] 첫 10초에 “1개 요청 → 합성 세계 2개·₩98,000”이라는 인간 피해가 보인다.
- [x] 타깃은 side-effect Agent를 배포하는 개발팀과 제품 책임자로 한정된다.
- [x] 보호 후 1개·₩49,000과 원래 목표 완료가 같은 화면에서 보인다.
- [x] block-all 정책을 거부해 “안전 = 기능 제거”가 아님을 증명한다.
- [x] 돈 외에도 커뮤니케이션·시간·데이터 failure family로 확장 가능한 같은 입력 path를 보여준다.

### Presentation Clarity · 10%

- [x] [`../slides/deck.md`](../slides/deck.md)는 정확히 5장, 120초다.
- [x] [`video-script.md`](video-script.md)는 정확히 120초 timeline과 shot list를 가진다.
- [x] [`demo-runbook.md`](demo-runbook.md)는 2+3+2분의 7분 cue를 가진다.
- [x] 모든 수치는 actual fixture rehearsal에서 왔고 synthetic badge와 함께 표시된다.
- [ ] 최종 PDF, 120초 영상, 7분 발표를 발표 장비에서 time-box rehearsal했다.

## 핵심 Q&A

| 예상 질문 | 20초 답변 | 바로 보여줄 evidence |
|---|---|---|
| **테스트 runner가 고정이면 Agent가 무엇을 하나요?** | “환경과 oracle은 재현성을 위해 고정돼 있습니다. Lab Agent는 입력에서 BehaviorProfile을 만들고, 어떤 fault를 먼저 주입할지 계획하며, 도구를 호출하고, evidence를 부검하고, 최소 보호책을 선택해 재실행합니다. 정답을 fixture에 미리 쓰는 대신 같은 hidden world가 pass/fail을 판정합니다.” | `experiment_plan`의 `tool_choice_reason`, `expected_evidence`, Raw `gym.tool_call`, `oracle.report` |
| **실제 행동 전에 사용자 승인을 받으면 되지 않나요?** | “승인은 알려진 위험한 행동에는 유용하지만, timeout 뒤 이미 commit된 결제를 다시 승인받는 식의 상태 불일치는 해결하지 못합니다. P0은 실제 action을 전혀 하지 않고, idempotency와 reconciliation 같은 실행 정책을 합성 환경에서 검증합니다. 실제 배포에서는 승인도 별도 방어선으로 남습니다.” | 첫 charge의 `committed=UNKNOWN`, status 조회 없는 retry, protected reconciliation |
| **LLM이 자기 결과를 채점하면 믿을 수 있나요?** | “채점하지 않습니다. controller가 소유한 hidden world, ledger, trace invariant가 verdict를 만듭니다. LLM은 bounded evidence를 마지막에 설명할 수 있지만 `verified_mitigation`을 선언할 권한은 없습니다.” | `oracle.report`, ledger refs, `protected_replay.gates`, LLM 호출 전 생성된 `finding_report` |
| **이 sandbox가 실제 Agent와 얼마나 같나요?** | “P0은 외부 코드를 실행하지 않습니다. pinned manifest로 선택한 synthetic behavior archetype만 측정하므로 실제 Agent의 취약점 발견이라고 말하지 않습니다. 대신 fault, seed, world, oracle을 결정적으로 만들어 제품 원리와 진단 계약을 증명합니다. framework 실행 adapter는 후속 범위입니다.” | `source_descriptor`, `execution_scope=synthetic_archetype`, residual/unsupported scope |
| **결제를 전부 막으면 가장 안전하지 않나요?** | “그 정책은 charge를 0건으로 만들지만 케이크 구매 목표도 실패합니다. 우리의 patch gate는 same-seed 안전뿐 아니라 benign control과 mission success를 요구하고 blanket block을 명시적으로 거부합니다.” | `blanket_block.mission_succeeded=false`, `blanket_block_rejected=true`, benign PASS |
| **3/3이면 안전하다는 뜻인가요?** | “아닙니다. 같은 seed에서 이 failure가 재현되고 이 patch가 통과했다는 뜻뿐입니다. stale status, 실제 provider, 미탐색 operator는 residual risk로 남고 실패 미발견은 안전 인증이 아닙니다.” | `reproduction_count=3`, `residual_risk`, `unsupported_scope` |
| **GitHub repo를 받는데 악성 코드는 어떻게 막나요?** | “clone, import, install, execute하지 않습니다. GitHub metadata로 ref를 SHA에 고정하고 최대 256 KB의 두 allowlist manifest 경로만 읽습니다. private token은 request header에만 있습니다.” | full resolved SHA, manifest path/hash, `SYNTHETIC ARCHETYPE` badge |
| **BehaviorProfile은 README를 요약한 성격 분석인가요?** | “아닙니다. retry, idempotency, reconciliation, untrusted input, loop budget을 각각 present·absent·unknown으로 기록합니다. verdict는 manifest field나 trace reference가 필요하고 README만 있으면 unknown입니다.” | profile 다섯 assessment와 evidence/unknown reason |
| **왜 먼저 duplicate payment를 고르나요?** | “현재 profile에 irreversible `payment.charge`가 있고 idempotency/reconciliation evidence가 비어 있으며, 돈 피해의 expected damage가 높기 때문입니다. 그 근거와 기대 evidence가 plan에 기록됩니다.” | selected nightmare의 `WHY`, `EXPECT`, `single_variable=true` |
| **Surprise Task는 별도 데모 지름길 아닌가요?** | “일반 데모와 같은 structured `target`, `POST /api/runs`, SSE, 실행 기록, 종료 판정을 씁니다. 재현할 수 없는 입력은 결제 예시로 바꾸지 않고 `unsupported`로 끝냅니다.” | 요청 본문 한 개, `/api/runs/{id}/events`, 끊기지 않는 기록 번호, 마지막 기록 1개 |
| **네트워크나 OpenAI가 실패하면 결과를 꾸미나요?** | “아닙니다. mode를 `offline_demo`로 표시하고 deterministic tool result를 같은 stream에 남깁니다. source preflight가 실패하면 외부 Agent를 분석했다고 주장하지 않습니다. 브라우저 fixture도 `AUTO / FIXTURE`를 숨기지 않습니다.” | 실제 rehearsed fallback event sequence와 mode badge |

## 피해야 할 답변

- “어떤 GitHub Agent든 실행해서 취약점을 찾습니다.”
- “3/3 통과했으니 안전합니다.”
- “GPT가 결과를 보고 맞는지 판단합니다.”
- “승인이 필요 없게 만들어 줍니다.”
- “실제 ₩98,000이 결제됐습니다.”
- “Surprise 다섯 Agent를 공격해 모두 통과했습니다.”
- “지원하지 않는 입력에서도 비슷한 실험을 해 줍니다.”

대신 다음 범위 문장을 사용한다.

> “고정된 manifest로 선택한 synthetic archetype에서 controller oracle이 실패와 보호책을
> 측정했습니다. 외부 repository code와 실제 계정은 실행하지 않았으며, 미탐색 범위는
> 안전 인증하지 않습니다.”

## 심사 직전 숫자 cross-check

| 항목 | 고정값 | 근거 |
|---|---:|---|
| 요청 | 케이크 1개, ≤ ₩50,000 | mission / permissions |
| 초기 합성 지갑 | ₩500,000 | world snapshot |
| 보호 전 | 2건, ₩98,000, 지갑 ₩402,000 | perturbed ledger/world |
| 보호 후 | 1건, ₩49,000, 지갑 ₩451,000 | protected ledger/world |
| runtime 위반 | 3개 | exactly-once, purchase count, total spend |
| same-seed 재현 | 3/3 | finding report |
| measured API rehearsal | 33 ordered events | 2026-08-01 offline explanation run |
| fixed Surprise | 5/5 | deterministic HTTP/SSE gate |
| domain Surprise | 5/5 | 돈·커뮤니케이션·시간·데이터·bonus rehearsal |

하나라도 현재 run과 다르면 slide나 영상 숫자를 유지하지 말고 새 측정값으로 갱신한다.
