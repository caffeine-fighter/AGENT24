# Worker B 킥오프 프롬프트 (Phase 0 공유 계약 + 이슈 #4)

> 아래 블록 전체를 새 Opus 5 세션에 붙여넣는다. Worker B 세션을 **먼저** 시작한다.

---

너는 NIGHTMARE LAB 해커톤 프로젝트의 구현 워커다. 오케스트레이터가 설계를 끝냈고, 너는 그 사양을 정확히 구현한다. 설계를 재발명하지 말 것.

## 필독 (구현 시작 전, 순서대로)

1. `docs/plans/issue-3-4-spec.md` — **단일 진실 소스. 모든 클래스·필드·함수 이름을 이 문서 그대로 사용한다.**
2. `docs/nightmare-lab-concept.md` — 제품 컨셉 (특히 자율 실험 루프, 진단 보고서 계약, MVP 범위)
3. `CLAUDE.md`, `AGENTS.md`
4. `gh issue view 4` — 담당 이슈 본문과 완료 조건

## 너의 임무 (2단계)

### Phase 0 — 공유 계약 (최우선, ~1.5–2h 목표)

브랜치 `agent/contracts` 생성 후 spec §2~§4, §7(Phase 0 부분) 그대로:

- `src/agent24/agent/models.py` — 전체 pydantic v2 모델
- `src/agent24/agent/protocols.py` — GymProtocol / PlannerProtocol / ExperimentPolicyProtocol
- `src/agent24/agent/fakes.py` — FakeGym + ScriptedAUT 4종 + ScriptedPlanner
- `src/agent24/agent/capabilities.py` — classify/find_risk_paths 시그니처 + 최소 keyword-table 구현
- `src/agent24/agent/__init__.py` — 계약 re-export
- `tests/unit/test_models.py`, `tests/unit/test_fakes.py`
- `tests/evals/cases.yaml` — spec §7의 6개 case 전부

완료 즉시 `scripts/verify.ps1` 통과 확인 → PR 생성. PR 본문에 반드시 포함: "C에게: `GymProtocol.run(scenario, policy)`을 이 계약대로 구현해 주세요 (같은 (scenario, policy) → 동일 RunResult 결정성 필수). 실제 Planner는 `prompts.load_prompt()` 본문 + `*Output` 모델을 Agents SDK output_type으로 사용." PR을 연 뒤 merge를 기다리지 말고 Phase 1로 진행한다.

### Phase 1 — 이슈 #4 (브랜치 `eval/oracle-gates`, `agent/contracts`에서 분기)

구현 순서 (각 모듈은 해당 단위 테스트와 같은 커밋으로):

1. `invariants.py` + `tests/unit/test_invariants.py`
2. `oracle.py` + `tests/unit/test_oracle.py` — 모든 Violation에 ledger_refs/trace_refs/state_path 필수, final_answer로 판정 금지
3. `divergence.py` + `tests/unit/test_divergence.py`
4. `minimize.py` + `tests/unit/test_minimize.py` — ddmin, max_runs cap, frozenset 캐시
5. `gates.py` + `tests/unit/test_gates.py` — `test_block_all_payments_patch_fails_benign_gate` 필수
6. `loop.py` (`run_lab`)
7. `tests/integration/test_full_loop.py` — 3개 end-to-end (duplicate payment / indirect injection / bounded retry), FakeGym + ScriptedPlanner만 사용

Worker A의 `DefaultExperimentPolicy`가 아직 없으므로 통합 테스트에는 **테스트 파일 안에 인라인 ExperimentPolicyProtocol stub**을 정의해 사용한다 (A의 파일을 만들거나 수정하지 말 것). 나중에 오케스트레이터가 교체를 지시한다.

## 절대 규칙

- **수정 가능 파일**: 위에 명시된 파일만. `policy.py`, `prompts.py`, `prompts/*`, `test_capabilities/policy/prompts.py`, `test_policy_pipeline.py`, `docs/prompt-log.md`는 Worker A 소유 — 절대 생성·수정 금지.
- Phase 0 완료 후 `models.py`/`protocols.py`/`fakes.py`/`cases.yaml`/`__init__.py`는 동결. Phase 1 중 계약 결함을 발견하면 **수정하지 말고 사용자에게 보고**한다.
- `src/agent24/agent/` 안에서 `openai`/`agents` import 금지. 모든 테스트는 네트워크 없이 결정적으로 실행되어야 한다 (`Date`/`random` 시드 고정 포함).
- 커밋: Conventional Commits (`feat(eval): ...`, `test(eval): ...`). 코드 스타일: ruff line-length 100, E/F/I/UP/B.
- PR 전 `.\scripts\verify.ps1` 통과 필수.

## 완료 기준 (이슈 #4 수용 조건 — PR 본문에 체크리스트로 붙이고 각 항목의 증거 테스트 이름 명시)

- [ ] 모든 finding이 구체적인 ledger·state·trace 증거를 인용한다 → `test_oracle.py`, `test_full_loop.py`
- [ ] 모든 결제를 차단하는 패치는 정상 control에서 실패한다 → `test_block_all_payments_patch_fails_benign_gate`
- [ ] 제거 가능한 fault가 있을 때 최소 반례가 원래보다 작다 → `test_minimize.py`
- [ ] eval 결과에 잔여 위험·미지원 범위가 표시된다 → `test_duplicate_payment_end_to_end` (residual_risk 검증)
- [ ] 테스트가 네트워크 없이 결정적으로 실행된다 → 전체 suite

막히면: spec을 다시 읽고, 그래도 모호하면 임의로 설계를 바꾸지 말고 질문을 정리해 사용자에게 보고한다.
