# Worker A 킥오프 프롬프트 — Wave 2: 이슈 #21 → #4 잔여분

> 아래 블록 전체를 Opus 5 세션에 붙여넣는다. main 최신에서 시작한다.
> Worker B(#20/#22)와 완전 병렬 — 파일이 전혀 겹치지 않는다.

---

너는 NIGHTMARE LAB 해커톤 프로젝트의 구현 워커다. Phase 0 공유 계약은 이미 main에 merge되어 있다 (`src/agent24/agent/models.py`, `protocols.py`, `fakes.py`, `capabilities.py`). **이 4개 파일과 `__init__.py`는 동결 상태 — 절대 수정하지 말고 import해서 사용한다.** 필요한 모델(`Invariant`의 check 대수, `Violation`, `OracleReport`, `Divergence`, `MinimizationResult`, `GateResult`, `PatchVerification`, `AntibodyPatch`, `canonical_json`, `run_digest`)은 전부 이미 정의되어 있다.

## 필독 (순서대로)

1. `docs/plans/issue-3-4-spec.md` §2, §5(결정적 알고리즘), §7(테스트) — 구현 사양의 원본
2. `docs/plans/phase0-contract-notes.md` — 계약 구현 노트
3. `src/agent24/agent/models.py`, `fakes.py` — 실제 코드. FakeGym의 시나리오 fixture 헬퍼(`retry_happy`+`commit_then_timeout`, `well_behaved` 등)를 테스트에서 그대로 재사용한다.
4. `gh issue view 21`, `gh issue view 4` — 완료 조건
5. `docs/nightmare-lab-concept.md` — "진단 보고서 계약", "Eval과 성공 게이트" 섹션

## 임무: 브랜치 `eval/oracle-divergence`, 아래 순서대로 (모듈+테스트 같은 커밋)

### Part 1 — 이슈 #21 (oracle + first divergence)

1. `src/agent24/agent/oracle.py` + `tests/unit/test_oracle.py`
   - `evaluate(invariants: Sequence[Invariant], run: RunResult) -> OracleReport` — check 타입별 순수 평가기 4종 (`ComparisonCheck`/`ExactlyOnceCheck`/`NoUntrustedToPrivilegedCheck`/`BoundedRepeatCheck`). 이슈 #21의 이름 매핑: `exactly_once_side_effect` = ExactlyOnceCheck, `maximum_total_spend`·`task_success` = ComparisonCheck(LedgerAggSelector).
   - Selector 해석은 순수 경로 조회: state path는 dot/숫자 인덱스, ledger는 tool 필터 + count/sum/max 집계.
   - **모든 `Violation`에 measured value(`actual`) + `ledger_refs`/`trace_refs`/`state_path` 증거 경로 필수. `final_answer`로 판정 금지.**
   - 결과 serialization은 `models.canonical_json`/`model_dump` 기반으로 canonical하게.
2. `src/agent24/agent/divergence.py` + `tests/unit/test_divergence.py`
   - `first_divergence(baseline: Sequence[TraceEvent], failing: Sequence[TraceEvent]) -> Divergence | None` — 이벤트를 `(kind, tool, args_digest, status)`로 정규화(call_id·payload 산문 무시), 첫 불일치 반환, prefix 소진 시 extra/missing_events.
3. 통합 fixture 검증 `tests/integration/test_oracle_pipeline.py` (FakeGym만, 네트워크 0):
   - `well_behaved` 정상 run → oracle 전부 통과
   - `retry_happy` + `commit_then_timeout` → `exactly_once`와 `maximum_total_spend` 위반, Violation이 실제 ledger seq·trace index 인용
   - **first divergence가 commit-후-timeout 이벤트(재시도 charge 직전 분기)를 가리킴**
   - 같은 fixture 2회 실행 → 결과 dump 동일 (결정성)

### Part 2 — 이슈 #4 잔여분 (minimization + patch gates)

4. `src/agent24/agent/minimize.py` + `tests/unit/test_minimize.py`
   - `async def minimize(scenario, reproduces: Callable[[Scenario], Awaitable[bool]], *, max_runs=24) -> MinimizationResult` — atom = 각 FaultSpec(`"fault:i"`) + 각 world_overrides 키(`"override:k"`). 고전 ddmin(청크·보수 제거, 축소 시 재시작, 아니면 granularity 배가), 결정적 순서, frozenset 캐시, max_runs 상한.
   - 테스트: decoy fault 2개 + 진짜 `commit_then_timeout` 1개 → 1 atom으로 축소 / 축소 불가 시나리오는 원본 유지 / `runs_used <= max_runs` / 결정성.
5. `src/agent24/agent/gates.py` + `tests/unit/test_gates.py`
   - `make_neighbors(failure: Scenario) -> list[Scenario]` — 순수·결정적: `at_call_index ± 1`, fault 순서 교체, 수치 world_override 미세 변경.
   - `async def verify_patch(patch, failure, gym: GymProtocol, invariants, *, benign, neighbors=None) -> PatchVerification` — ① 동일 seed 재실행 통과 ② 이웃 통과 ③ **benign control(무 fault + well_behaved + 패치 적용)에서 mission 완료 invariant 통과**. FakeGym의 policy layer가 패치 적용을 이미 지원한다.
   - **필수 테스트 `test_block_all_payments_patch_fails_benign_gate`**: `max_purchase_count=0` 패치는 benign에서 실패 → `accepted=False`.
6. `tests/evals/cases.yaml`의 기존 `oracle-*` 3개 항목을 너의 실제 테스트 node id로 갱신 (이 3개 항목만 네 소유 — `policy-*` 항목은 Worker B가 병렬로 수정 중이니 절대 건드리지 않는다). 기존 항목이 가리키는 `test_full_loop.py`는 이번 wave 범위가 아니므로, command를 네가 실제 구현한 테스트로 교체하고 expectation도 맞춘다.

주의: `loop.py`(전체 자율 루프)는 이번 wave에서 구현하지 않는다 — Worker B의 planner(#22)와 보고서(#23)가 merge된 뒤 다음 wave에서 조립한다. patch의 gym 실제 replay(#24)는 C 담당이므로 너는 `GymProtocol` 경유로만 호출한다.

## 절대 규칙

- 수정 가능: `oracle.py`, `divergence.py`, `minimize.py`, `gates.py`, 위 테스트 4개 + `test_oracle_pipeline.py`, cases.yaml의 oracle-* 3항목. **그 외 금지.** 특히 `profile.py`/`planner.py`와 그 테스트는 Worker B가 병렬 작업 중 — 생성도 금지.
- 동결 파일(`models.py` 등) 결함 발견 시 수정하지 말고 사용자에게 보고.
- `src/agent24/agent/` 안에서 `openai`/`agents` import 금지. 모든 테스트는 네트워크 없이 결정적 (이슈 #21 완료 조건).
- 커밋: Conventional Commits(`feat(eval): ...`, `test(eval): ...`). PR 전 `.\scripts\verify.ps1` 통과.
- PR 본문에 이슈 #21 완료 조건 체크리스트 + 각 항목의 증거 테스트 이름 명시. `Closes #21`, `Refs #4` (#4는 minimization/gates까지 포함하므로 이 PR로 대부분 충족되지만 loop 조립 전까지 열어 둔다).

막히면: spec §5를 다시 읽고, 그래도 모호하면 설계를 임의로 바꾸지 말고 질문을 정리해 사용자에게 보고한다.
