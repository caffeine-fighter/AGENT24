# Agent boundary

Lab Agent(실패 과학자)의 계약과 결정적 알고리즘이 사는 경계입니다.
이 패키지 안에서는 `openai` / `agents`를 import하지 않습니다. LLM은
`PlannerProtocol` 뒤에만 존재하고, 실제 sandbox는 `GymProtocol` 뒤에만
존재하므로 나머지는 네트워크 없이 결정적으로 실행됩니다.

## 공유 계약 (Phase 0 — 동결됨)

| 파일 | 역할 |
|---|---|
| `models.py` | 모든 pydantic v2 모델, 표준 invariant id, `args_digest`/`run_digest` |
| `protocols.py` | `GymProtocol`, `PlannerProtocol`, `ExperimentPolicyProtocol` |
| `fakes.py` | `FakeGym`, `ScriptedAUT` 4종, `ScriptedPlanner` |
| `capabilities.py` | 도구 분류(`classify`)와 위험 경로 탐지(`find_risk_paths`) |
| `invariants.py` | Life-v0 controller-owned task/platform 불변식 |
| `loop.py` | baseline → fault → oracle → 최소화 → patch gate → report 단일 루프 |
| `participant_intake.py` | owner/static provenance, pinned path/blob/line evidence, compatibility-only report (#62) |

이 네 파일과 `tests/evals/cases.yaml`, `__init__.py`는 **양쪽 워커의 합의 없이
변경하지 않습니다.** 계약 결함을 발견하면 고치기 전에 먼저 보고합니다.
배경과 결정 근거는 `docs/plans/phase0-contract-notes.md`에 있습니다.

세 가지 의미 규칙이 알고리즘이 아니라 계약에 박혀 있습니다.

- **effective call**: 짝이 되는 결과 status가 `blocked_by_policy`가 아닌 호출.
  trace를 읽는 검사는 effective call만 셉니다. 차단된 호출은 패치가 동작했다는
  증거로 trace에 남습니다.
- **`ExactlyOnceCheck.group_by`**: `LedgerEntry` 속성 → `entry.data[key]` →
  `args_digest` 순으로 해석합니다. 마지막 fallback이 key 없는 중복 결제를
  잡아냅니다.
- **`DenyRule`**: 호출이 선언한 provenance와의 교집합으로 판정합니다.
  따라서 "전부 차단"하려면 `USER_INSTRUCTION`을 명시해야 합니다.

## 이슈 #3 — 실험 정책과 구조화 프롬프트

- `profile.py`: 관찰된 `BehaviorProfile`. 근거 없는 판정은 스키마가 거부한다 (#20)
- `planner.py`: 위험도 점수와 결정적 실험 선택 (#22)
- `report.py`: 증거에서 유도되는 terminal state (#23)
- `prompts.py` + `prompts/*.md`: 버전이 있는 지시문, frozen 출력 스키마 매핑,
  turn·비용·종료 조건(`should_stop`), schema 실패 복구, untrusted fence

프롬프트가 인용하는 값(예산, gym 도구, invariant id, 금지 표현)은 전부 강제하는
코드에서 치환됩니다. 지시문과 실제 규칙이 조용히 어긋나지 않게 하려는 것이고,
`tests/unit/test_prompts.py`가 그 일치를 검사합니다.

## 이슈 #26 one-input 조립

`loop.py`는 외부 source를 실행하지 않고 manifest가 선택한 allowlisted Life-v0
synthetic behavior archetype만 측정합니다. 이 제한은 `FindingReport.residual_risk`와
`LabReport.unsupported_scope`에 항상 남습니다. 동일 seed 3회 재현, 최초 divergence,
최소 반례, same-seed/neighbor/benign patch gate를 하나의 결정적 결과로 조립합니다.

오케스트레이터는 UI나 특정 외부 서비스에 직접 의존하지 않게 유지합니다.

## 이슈 #62 participant intake

`participant_intake.py`는 exact `repository@SHA`에 등록된 최대 4개 evidence path의
Git metadata만 확인한다. owner manifest가 있으면 `OWNER MANIFEST`가 항상 우선하고,
없을 때만 `LAB-INFERRED STATIC PROFILE`을 사용한다. branch/blob drift, missing path,
oversized file, symlink/submodule, traversal, secret-like path는 profile 재사용 없이
`unsupported`로 끝난다. 이 경로의 report는 항상 experiments 0, findings 0이며
compatibility를 target의 확인된 취약점으로 표현하지 않는다.
