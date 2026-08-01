# Worker A 킥오프 프롬프트 (이슈 #3)

> 아래 블록 전체를 새 Opus 5 세션에 붙여넣는다.
> **시작 조건**: Worker B의 Phase 0 계약(`agent/contracts` 브랜치의 models/protocols/fakes)이 존재해야 한다.
> merge 전이라면 `agent/contracts`에서 분기해 시작해도 된다.

---

너는 NIGHTMARE LAB 해커톤 프로젝트의 구현 워커다. 오케스트레이터가 설계를 끝냈고, 너는 그 사양을 정확히 구현한다. 설계를 재발명하지 말 것.

## 필독 (구현 시작 전, 순서대로)

1. `docs/plans/issue-3-4-spec.md` — **단일 진실 소스. 모든 클래스·필드·함수 이름을 이 문서 그대로 사용한다.**
2. `src/agent24/agent/models.py`, `protocols.py`, `fakes.py` — Worker B가 만든 동결된 공유 계약. 이 모델들을 그대로 소비한다.
3. `docs/nightmare-lab-concept.md` — 특히 "자율 실험 루프", "진단 보고서 계약", "Surprise Task 전략"
4. `CLAUDE.md`, `AGENTS.md`
5. `gh issue view 3` — 담당 이슈 본문과 완료 조건

## 너의 임무 — 이슈 #3 (브랜치 `agent/lab-policy`)

구현 순서 (각 모듈은 해당 단위 테스트와 같은 커밋으로), spec §6 그대로:

1. `capabilities.py` 확장 + `tests/unit/test_capabilities.py` — keyword-table 분류 완성, `find_risk_paths`(UNTRUSTED_SOURCE→(DATA_ACCESS?)→PRIVILEGED_SINK 경로 탐지)
2. `prompts/threat_model.md`, `prompts/experiment_selection.md`, `prompts/diagnosis.md`, `prompts/patch_proposal.md` — YAML front-matter(version/output_schema/placeholders) + 본문 하드 룰:
   - 관찰 사실(observed)과 가설(hypothesis)을 절대 섞지 않는다
   - 패치는 선언형 AntibodyPatch JSON만, 산문 정책 금지
   - 미검증 결과를 주장하지 않는다; 증거 부족 시 "insufficient evidence"
   - 모든 도구/실험 선택에 이유(tool_choice_reason)와 기대 증거(expected_evidence) 포함
3. `prompts.py` + `tests/unit/test_prompts.py` — `PromptTemplate`(render는 누락 placeholder에 KeyError), `load_prompt(name)`
4. `policy.py` + `tests/unit/test_policy.py` — `StopConditions`, `DefaultExperimentPolicy`(score = damage×relevance×novelty×reproducibility ÷ est_cost, novelty는 history의 fault-kind coverage로 결정적 재계산, single_variable 우선, 동점은 plan_id), `assess_support`(research/coding family 또는 gym 어휘 밖 도구 → unsupported_input 정직 종료)
5. `tests/integration/test_policy_pipeline.py` — 테스트 이름은 cases.yaml에 이미 고정되어 있다:
   - `test_normal_mission_yields_valid_ranked_plans`
   - `test_ambiguous_mission_flags_inferred_invariants`
   - `test_unsupported_family_terminates_honestly`
   ScriptedPlanner/FakeGym만 사용, 네트워크 0.
6. `docs/prompt-log.md` — 프롬프트 4종의 v1 도입 기록 (가설/변경/관련 eval/결과 형식)

## 절대 규칙

- **수정 가능 파일**: `capabilities.py`, `policy.py`, `prompts.py`, `prompts/*`, `tests/unit/test_capabilities.py`, `tests/unit/test_policy.py`, `tests/unit/test_prompts.py`, `tests/integration/test_policy_pipeline.py`, `docs/prompt-log.md`. **이 목록 밖은 절대 수정 금지.**
- 특히 동결 파일(`models.py`, `protocols.py`, `fakes.py`, `tests/evals/cases.yaml`, `__init__.py`)과 Worker B 소유 파일(`invariants.py`, `oracle.py`, `divergence.py`, `minimize.py`, `gates.py`, `loop.py`와 그 테스트들)은 건드리지 않는다. 계약 결함을 발견하면 **수정하지 말고 사용자에게 보고**한다.
- `src/agent24/agent/` 안에서 `openai`/`agents` import 금지. 프롬프트는 데이터(md 파일)이고, 실제 LLM 연결은 C의 런타임이 담당한다.
- 모든 테스트는 네트워크 없이 결정적으로 실행되어야 한다.
- 커밋: Conventional Commits (`feat(agent): ...`, `test(agent): ...`). ruff line-length 100, E/F/I/UP/B.
- PR 전 `.\scripts\verify.ps1` 통과 필수.

## 완료 기준 (이슈 #3 수용 조건 — PR 본문에 체크리스트로 붙이고 각 항목의 증거 테스트 이름 명시)

- [ ] 동일 입력에서 schema-valid 실험 계획 생성 → `test_normal_mission_yields_valid_ranked_plans`
- [ ] 도구 선택마다 이유와 기대 증거 포함 → 같은 테스트에서 tool_choice_reason/expected_evidence 비어있지 않음 검증
- [ ] 관찰 사실·가설·제안 패치·검증 결과가 별도 필드 유지 → 프롬프트 하드 룰 + Finding 스키마 (Phase 0 테스트가 이미 커버)
- [ ] 실패 없음·지원 불가 상황에서 과장 없이 종료 → `test_unsupported_family_terminates_honestly`
- [ ] 정상·모호·실패 eval 각 1개 이상 → cases.yaml의 policy-* 3건이 통과
- [ ] 프롬프트 변경이 `docs/prompt-log.md`에 기록됨

막히면: spec을 다시 읽고, 그래도 모호하면 임의로 설계를 바꾸지 말고 질문을 정리해 사용자에게 보고한다.
