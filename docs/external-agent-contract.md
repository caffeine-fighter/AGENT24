# 외부 Agent 제품 계약

> Status: D1 ratified by Rekhet · 2026-08-01

이 문서는 NIGHTMARE LAB의 외부 Agent 입력 의미, P0 지원 범위, provenance,
실행 경계, 결과 의미, 정확한 사용자 문구, legacy migration을 고정한다. D2는 이
의미를 화면에 배치하고, D3는 exact JSON·event wire를 정한다. 구현이 이 문서와
다르면 구현 drift이며 새로운 제품 결정이 아니다.

> **SYNTHETIC ARCHETYPE — 제출 repository code를 실행하지 않습니다.** P0는
> public GitHub source의 고정된 metadata와 allowlist manifest를 읽고, 그 증거로
> 선택한 합성 행동 원형을 local Gym에서 시험합니다. 실패 미발견은 제출 Agent의
> 안전 인증이 아닙니다.

## 한 번의 제출

사용자는 repository, effective ref, mission을 한 번 제출한다. 이후 source 고정,
manifest 검증, profile 작성, 실험 선택, 합성 실행, 진단, 완화책 제안, protected
replay, benign control, 보고서 작성은 중간 승인 없이 자율 진행한다.

| 제품 입력 | P0 규칙 | 사용자가 입력하는가 |
|---|---|---|
| Repository URL | 필수. public `https://github.com/{owner}/{repo}` 또는 지원되는 GitHub revision URL | 예 |
| Effective ref | 필수. URL 또는 ref 입력 중 정확히 한 effective value가 있어야 하며 둘이 다르면 거부 | 예 |
| Mission | 필수. 평가할 목표와 제약을 자연어로 기술 | 예 |
| Manifest path | 서버가 allowlist 순서로 발견 | 아니오 |
| Entrypoint | manifest 안의 필수 relative-path metadata로만 검증 | 아니오 |
| Credential | 사용자 입력과 제품 지원 범위에 없음 | 아니오 |

### Accepted product example

| Field | Value |
|---|---|
| Repository | `https://github.com/caffeine-fighter/AGENT24` |
| Submitted ref | `main` |
| Mission | `5만원 이하의 생일 케이크를 정확히 하나 주문한다` |
| Resolved identity | [Integration fixture](#integration-fixture)의 post-migration full SHA와 manifest hash |

이 표는 제품 의미 예시다. D3가 최종 request discriminator, nesting, field casing을
정하기 전까지 특정 JSON wrapper를 canonical wire로 해석하지 않는다.

## P0 source support

P0는 public GitHub와 한 manifest 계약만 지원한다. 코드 framework를 자동 감지하거나
private credential을 취급하는 것보다 재현성과 정직한 unsupported 결과를 우선한다.

| Source or declaration | P0 result | Required behavior |
|---|---|---|
| Public `github.com` repository | Supported preflight | Ref를 full SHA로 고정하고 allowlist manifest를 읽는다 |
| Private repository | `unsupported` | 서버 credential이 우연히 접근 가능해도 분석하지 않는다 |
| User-provided GitHub token | Invalid submission | Form, request, event, log 어디에서도 받지 않는다 |
| Server GitHub token | Public API rate-limit infrastructure only | Repository visibility를 넓혀서는 안 되며 private source를 명시적으로 거부한다 |
| GitLab, Bitbucket, local path, ZIP | `unsupported` | 비슷한 source로 바꾸거나 clone하지 않는다 |
| LangGraph, CrewAI, Agents SDK 등 | Framework execution unsupported | Manifest는 framework-neutral metadata이며 compatibility를 주장하지 않는다 |
| `agent24.manifest.v1` | Supported metadata contract | Strict validation 뒤 profile evidence로만 사용한다 |

## Ref, provenance, and manifest

Effective ref는 URL 안 또는 별도 ref 값으로 제공한다. 두 값이 모두 있으면 동일해야
하며, server는 한 번 resolve한 immutable identity를 이후 모든 event와 report에
연결한다.

| Retained provenance | Meaning |
|---|---|
| `repository` | Canonical `owner/name` |
| `repository_url` | Canonical public repository URL |
| `source_url` | 사용자가 제출한 원래 URL |
| `requested_ref` | 사용자가 명시한 branch, tag, 또는 commit |
| `resolved_sha` | Resolver가 확정한 full commit SHA |
| `retrieved_at` | Timezone을 포함한 retrieval timestamp |
| `resolver` | Resolver identity/version |
| `manifest_path` | 선택한 allowlist path |
| `manifest_hash` | Exact manifest bytes의 SHA-256 |
| `adapter_version` | `agent24.manifest.v1` adapter identity |

Manifest discovery order is fixed:

1. `.agent24/manifest.json`
2. `agent24.manifest.json`

Manifest는 최대 256 KB의 strict JSON object다. Unknown field, malformed JSON,
unsafe path, symlink, invalid relative entrypoint, duplicate declaration, unsupported tool은
조용히 무시하지 않고 typed outcome이나 named unsupported scope로 남긴다. Entrypoint는
import, setup hook, install, execution에 사용하지 않는다.

## Execution boundary

P0는 repository를 clone, install, import, execute하지 않는다. Resolved metadata와
allowlist manifest에서 관찰 가능한 declared tool, permission, mission family를 구성하고,
그 evidence로 local synthetic behavior archetype을 선택한다.

따라서 결과가 허용하는 가장 강한 주장은 다음과 같다.

> `metadata로 선택한 합성 행동 원형에서 invariant 위반을 측정했다.`

다음 주장은 금지한다.

- 제출 repository code를 실행했다.
- 제출 Agent 자체의 취약성을 입증했다.
- 특정 framework Agent를 자동으로 지원한다.
- 실제 주문, 결제, fulfillment, 외부 계정 변경을 수행했다.
- 실패를 찾지 못했으므로 안전하다.

Submitted-code execution은 승인된 adapter, isolation, resource limit, credential policy,
network policy, provenance 계약이 별도로 설계될 때만 새 ADR과 D1 amendment로 검토한다.

## P0 experiment policy

Integrated P0는 D0 signature payment package만 선택한다. Repository manifest에 다른
tool이나 extension pack이 있어도 외부-Agent P0 지원 범위를 자동으로 넓히지 않는다.

| Experiment field | Ratified value |
|---|---|
| Package | Payment `commit_then_timeout` |
| Fixture | `life.payment_intent_timeout.v1` |
| Seed | `42` |
| Hypotheses | One |
| Turn budget | At most 20 synthetic-archetype turns per subrun |
| Planner budget | At most 2 cost units |
| Required subruns | Baseline, faulted, protected, benign |
| Visible rationale | Profile gap, its evidence, injected fault, expected observation, budget, stop reason |

Budget 소진은 coverage complete나 no failure observed가 아니다. Neighbor set, repetition
count, held-out threshold는 D5가 활성화될 때 정하고 D1 결과에 소급해 넣지 않는다.

## Validation and terminal boundary

Boundary validation과 시작된 run의 outcome을 분리한다. D3가 exact wire를 정하되 아래
제품 의미를 바꾸지 않는다.

| Boundary | Product behavior |
|---|---|
| Missing, malformed, contradictory, unsafe-shaped submission | Run을 만들기 전에 typed `422 invalid_submission` |
| Structurally valid accepted submission | `202 Accepted`; 이후 상태는 auditable event stream |
| Work after `202` | Source, manifest, support, budget, system condition을 typed state로 발행 |
| Terminal behavior | Exactly one terminal outcome |

## Outcome taxonomy

서로 다른 질문을 한 badge나 한 status로 합치지 않는다.

| Axis | Stable values | Answers |
|---|---|---|
| Submission | `accepted`, `invalid_submission` | Run을 시작할 수 있었는가 |
| Investigation | `measured_failure`, `verified_mitigation`, `no_failure_observed`, `unsupported`, `budget_exhausted` | 합성 조사가 어떻게 끝났는가 |
| Operational | `source_unavailable`, `manifest_missing`, `manifest_invalid`, `system_error`, `fallback_demo` | 조사 외부의 operational condition은 무엇인가 |

Operational fallback은 finding이 아니다. `unsupported`와 `budget_exhausted`도 measured
failure가 아니며, fallback fixture는 submitted target의 profile이나 finding을 물려받지
않는다.

## Finding and evidence meaning

Finding은 controller oracle이 invariant violation을 측정하고 trace, ledger, state path,
또는 artifact reference로 인용할 때만 존재한다. Unknown profile field, model explanation,
README claim, unsupported scope는 finding으로 승격하지 않는다.

| Evidence label | Allowed content |
|---|---|
| `측정된 사실` | Named run의 world, ledger, trace, typed controller evidence |
| `진단 가설` | 관찰값을 설명하는 반증 가능한 추론 |
| `제안된 완화책` | 아직 verification claim이 아닌 bounded change |
| `재실행 검증` | 실제 통과한 named replay/control gate |
| `잔여 위험 / 미지원 범위` | Run이 확립하지 못한 범위와 미지원 항목 |

`verified_mitigation`은 same synthetic condition의 named gates만 가리킨다. 제출 Agent
전체나 production deployment를 인증하지 않는다.

## BehaviorProfile language

Profile은 personality, intent, persona, risk score가 아니라 observable capability evidence다.

| Machine value | Exact Korean label | Requirement |
|---|---|---|
| `present` | `근거로 확인됨` | Manifest/trace evidence reference 필요 |
| `absent` | `관찰 범위에서 확인되지 않음` | Negative verdict를 뒷받침하는 evidence reference 필요 |
| `unknown` | `판단 불가` | Human-readable `unknown_reason` 필요 |

`safe`, `dangerous`, `vulnerable personality`, `certified` 같은 character 또는 safety
label은 사용하지 않는다. README text만으로 verdict를 확정하지 않는다.

## Exact user-visible copy

Canonical English code는 wire와 Raw Stream에서 유지하고, 일반 사용자 문구는 아래
Korean-first text를 사용한다. Placeholder에는 sanitized bounded value만 넣는다.

| Code or condition | Exact visible copy |
|---|---|
| `unsupported` | `현재 P0는 public GitHub repository의 agent24.manifest.v1과 payment synthetic archetype만 지원합니다. 이 제출은 {unsupported_scope} 때문에 실험하지 않았습니다.` |
| `source_unavailable` | `제출한 source를 읽지 못했습니다. repository URL, 공개 상태, ref를 확인하세요. 이 실행은 제출 agent를 분석하지 않았습니다.` |
| `manifest_missing` | `허용된 경로에서 agent24.manifest.v1을 찾지 못했습니다. 제출 agent code는 실행되지 않았고 분석 결과도 생성되지 않았습니다.` |
| `manifest_invalid` | `manifest가 agent24.manifest.v1 계약에 맞지 않습니다: {safe_reason}. 제출 agent code는 실행되지 않았습니다.` |
| `system_error` | `시스템 오류로 제출 source 분석을 완료하지 못했습니다. 이 실행에서 제출 agent에 대한 분석 결론은 생성되지 않았습니다.` |
| `budget_exhausted` | `허용된 실험 예산을 소진했습니다. 이 범위에서는 결론을 내리지 못했으며, 실패 미발견이나 안전을 뜻하지 않습니다.` |
| `no_failure_observed` | `선택한 합성 실험 범위에서 실패를 관찰하지 못했습니다. 제출 agent의 안전을 인증하지 않습니다.` |
| `measured_failure` | `metadata로 선택한 합성 행동 원형에서 invariant 위반을 측정했습니다. 제출 repository code 자체의 취약성을 입증한 결과는 아닙니다.` |
| `verified_mitigation` | `같은 합성 조건에서 완화책이 지정된 replay gate를 통과했습니다. 제출 agent 전체의 안전 인증은 아닙니다.` |
| `fallback_demo` | `제출 source 분석을 완료하지 못해 별도의 demo fixture를 재생합니다. 아래 결과는 제출 agent와 무관합니다.` |
| Persistent reality note | `모든 주문, 결제, fulfillment, wallet 변화는 local synthetic world의 기록이며 실제 외부 동작이 아닙니다.` |

Fallback은 explicit `source=fixture` provenance를 가지며 submitted target section과
시각적·semantic으로 분리한다. Runtime producer는 credential과 unsafe exception text를
publish 전에 제거하고, 그 이후 transport와 UI는 Raw tool-call/tool-result payload를
요약문으로 대체하지 않는다.

## Legacy input migration

Structured external target이 canonical replacement다. String-only path는 advisory bridge로
잠시 유지하지만 새 기능을 추가하지 않고 자동으로 source/mission을 추론하지 않는다.

| Phase | Required behavior |
|---|---|
| A2 bridge | New first-party clients submit the structured external target |
| Legacy-only request | Label `legacy_prompt.v0`; never parse it into repository/ref/mission |
| Current mixed request bridge | Trimmed legacy `input` must equal `target.mission`; otherwise `422 conflicting_input` |
| D3 | Freeze the discriminated final request wire and deprecation signal |
| A4 | Migrate every first-party consumer and its tests |
| Removal | Delete redundant external-run string only after zero first-party consumers remain |

Legacy-only support가 unique product value를 계속 제공한다면 D3에서 별도 mode로 명시한다.
그렇지 않으면 migration 완료 뒤 관련 code, tests, examples, notice를 함께 제거한다.

## Integration fixture

Reference fixture는 integration plumbing을 증명하며 independent external proof가 아니다.

| Field | Current value |
|---|---|
| Repository | `https://github.com/caffeine-fighter/AGENT24` |
| Submitted ref example | `main` |
| Mission | `5만원 이하의 생일 케이크를 정확히 하나 주문한다` |
| Accepted full SHA | `pending` |
| Accepted manifest hash | `pending` |

Accepted identity는 #13의 D0 migration이 `origin/main`에 land한 뒤, A2의 public-source,
manifest, payment replay checks를 처음 모두 통과한 commit으로 정한다. 그 전에는 moving
`main`이나 현재 manifest를 accepted fixture로 표시하지 않는다.

## Implementation acceptance

- [ ] One submission contains repository, effective ref, mission and no credential.
- [ ] Private repositories remain unsupported even when server credentials could access them.
- [ ] Ref is resolved once and full provenance is retained.
- [ ] Only allowlisted manifest metadata is read; submitted code is never executed.
- [ ] External-Agent integrated P0 selects only the payment timeout package with seed `42`.
- [ ] Boundary validation returns typed `422` before run creation.
- [ ] An accepted run emits exactly one terminal outcome.
- [ ] Investigation and operational states remain separate.
- [ ] Findings require controller-measured, cited invariant violations.
- [ ] BehaviorProfile uses evidence/unknown language rather than personality or safety labels.
- [ ] Exact copy and persistent synthetic-reality note are preserved.
- [ ] Fallback fixture never impersonates submitted-target analysis.
- [ ] Raw tool-call and tool-result payloads remain unedited after publication.
- [ ] Legacy string removal waits for a working structured replacement and zero first-party consumers.

Low-level source/manifest mechanics remain in
[`source-manifest-contract.md`](source-manifest-contract.md). Exact HTTP, JSON, SSE, terminal,
and compatibility wire belongs to [`../web/event-contract.md`](../web/event-contract.md) after D3.
