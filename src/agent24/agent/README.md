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

## 이슈 #3 (Worker A)

- `policy.py`: 위험도 점수, 실험 랭킹, `StopConditions`, 정직한 종료
- `prompts.py` + `prompts/*.md`: 버전이 있는 지시문과 출력 스키마 매핑

## 이슈 #4 (Worker B)

- `invariants.py` → `oracle.py` → `divergence.py` → `minimize.py` →
  `gates.py` → `loop.py`

오케스트레이터는 UI나 특정 외부 서비스에 직접 의존하지 않게 유지합니다.
