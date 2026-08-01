# Worker B 킥오프 프롬프트 — Wave 2: 이슈 #20 → #22

> 아래 블록 전체를 Opus 5 세션에 붙여넣는다. main 최신에서 시작한다.
> #22는 #20의 BehaviorProfile에 의존하므로 **한 세션이 #20 → #22 순서로** 구현한다.

---

너는 NIGHTMARE LAB 해커톤 프로젝트의 구현 워커다. Phase 0 공유 계약은 이미 main에 merge되어 있다 (`src/agent24/agent/models.py`, `protocols.py`, `fakes.py`, `capabilities.py`). **이 4개 파일과 `__init__.py`는 동결 상태 — 절대 수정하지 말고 import해서 사용한다.** 새 모델이 필요하면 네가 만드는 새 파일 안에 정의한다.

## 필독 (순서대로)

1. `docs/plans/issue-3-4-spec.md` §2–§4 — 동결 계약의 모델·프로토콜·FakeGym 사양
2. `docs/plans/phase0-contract-notes.md` — 계약 구현 노트
3. `src/agent24/agent/models.py`, `fakes.py` — 실제 코드 (특히 `ExperimentPlan`, `Hypothesis`, `StopDecision`, FakeGym의 AUT 프로필 4종과 시나리오 fixture 헬퍼)
4. `gh issue view 20`, `gh issue view 22` — 담당 이슈 본문·완료 조건 (epic #13의 컨텍스트는 `gh issue view 13`)
5. `docs/nightmare-lab-concept.md` — "자율 실험 루프", "Surprise Task 전략" 섹션

## 배경: 제품 방향이 확장됐다

Epic #13: 이제 입력은 **외부 Agent의 GitHub repo**다. C가 SourceDescriptor(#16)와 AgentManifest 로더(#18)를 만들고, 너는 그 manifest + 관찰된 baseline trace에서 **BehaviorProfile**을 만들고(#20), profile을 근거로 **P0 Nightmare 실험 하나를 선택**한다(#22). C의 #18이 아직 미완이므로 manifest 타입은 네가 새 파일에 정의하고, PR 본문으로 C에게 계약을 통보한다.

## Part 1 — 이슈 #20: BehaviorProfile (브랜치 `agent/behavior-profile`)

새 파일 `src/agent24/agent/profile.py` + `tests/unit/test_profile.py`:

- `AgentManifest` pydantic 모델 (이 파일에 정의): `name`, `source_ref: str`(pinned commit/URL), `readme_text: str`, `system_prompt: str`, `tools: list[ToolSpec]`(동결 모델 재사용), `permissions: dict[str, JsonValue]`
- `EvidenceRef`: `kind: Literal["manifest", "trace"]`, `path: str`(manifest 필드 경로) 또는 `trace_index: int`
- `CapabilityAssessment`: `value: Literal["present", "absent", "unknown"]`, `evidence: list[EvidenceRef]`, `unknown_reason: str | None` — **증거 없으면 반드시 unknown + 이유**
- `BehaviorProfile`: `protected_assets: list[str]`, `side_effect_tools: list[str]`, `permissions`, 그리고 capability 필드 5종 — `retry_behavior`, `idempotency_usage`, `reconciliation_usage`, `untrusted_input_handling`, `loop_budget` (각각 `CapabilityAssessment`)
- `build_behavior_profile(manifest: AgentManifest, baseline: RunResult | None) -> BehaviorProfile` — 순수 함수. manifest에서 도구·권한 추출(capabilities.classify 재사용), baseline trace가 있으면 idempotency key 사용 여부·payment.status 호출 여부·반복 패턴을 trace 증거로 판정
- **하드 룰**: README 문구만으로 취약성을 확정하지 않는다(README는 unknown 근거로만). 모든 분류값에 EvidenceRef 또는 unknown_reason이 있어야 한다 — 스키마 validator로 강제.

테스트: 정상 manifest+trace → schema-valid profile / 불완전 manifest(도구 목록만) → 해당 필드 전부 unknown / 빈 trace → trace 기반 필드 unknown. baseline trace fixture는 FakeGym `well_behaved` 실행으로 생성.

완료 시 `scripts/verify.ps1` 통과 → PR 생성. **PR 본문에 C(#18) 앞으로**: "manifest 로더는 `profile.AgentManifest`를 산출해 주세요. 필드 협의 필요 시 이 PR에 코멘트."

## Part 2 — 이슈 #22: P0 ExperimentPlan 선택 (브랜치 `agent/p0-planner`, Part 1에서 분기)

새 파일 `src/agent24/agent/planner.py` + `tests/unit/test_planner.py` + `tests/integration/test_planner_pipeline.py`:

- `SUPPORTED_OPERATORS`: allowlist — 현재 gym이 지원하는 fault operator(`commit_then_timeout`, `malicious_web_content`, `empty_result`)와 oracle capability의 조회 테이블. **fixed keyword 매칭이 아니라 BehaviorProfile 필드를 근거로** 선택한다.
- `select_p0_experiment(profile: BehaviorProfile, mission: Mission) -> ExperimentPlan | StopDecision`:
  - payment 계열 side_effect_tool 존재 + `idempotency_usage`가 absent/unknown → `commit_then_timeout` on `payment.charge` 선택 (P0 signature)
  - 선택 이유는 `tool_choice_reason`에, 기대 관찰값은 `expected_evidence`에 — profile의 어떤 필드·증거가 근거인지 명시
  - rollout/tool-call budget은 `Scenario.max_turns` + `Hypothesis.est_cost_units`로 기록
  - 지원 operator가 없으면 `StopDecision(stop=True, reason="unsupported_input", detail=...)` — 결과 조작 금지
- 통합 테스트 3종 (FakeGym fixture만, 네트워크 0): 정상 profile → commit_then_timeout 계획 / 모호 profile(unknown 다수) → 계획은 나오되 이유에 unknown 근거 명시 / 미지원(도구가 gym 어휘 밖) → unsupported 종료
- `tests/evals/cases.yaml`의 기존 `policy-*` 3개 항목을 **너의 실제 테스트 node id로 갱신** (id는 `planner-normal`, `planner-ambiguous`, `planner-unsupported`로 교체 가능 — 이 3개 항목은 네 소유. `oracle-*` 항목은 Worker A 소유이므로 건드리지 않는다)
- agent instruction(프롬프트)을 추가·변경했다면 `docs/prompt-log.md`에 기록. 순수 결정적 정책만 구현했다면 그 사실을 한 줄 기록.

## 절대 규칙

- 수정 가능: `profile.py`, `planner.py`, 위에 명시한 테스트 3개, cases.yaml의 policy-* 3항목, `docs/prompt-log.md`. **그 외 금지.** 특히 `oracle.py`/`divergence.py`/`minimize.py`/`gates.py`와 그 테스트는 Worker A가 병렬 작업 중 — 생성도 금지.
- 동결 파일 결함 발견 시 수정하지 말고 사용자에게 보고.
- `src/agent24/agent/` 안에서 `openai`/`agents` import 금지. 모든 테스트는 네트워크 없이 결정적.
- 커밋: Conventional Commits(`feat(agent): ...`), 모듈+테스트 같은 커밋. PR 전 `.\scripts\verify.ps1` 통과.
- PR 본문에 이슈 완료 조건 체크리스트 + 각 항목의 증거 테스트 이름 명시. `Closes #20` / `Closes #22`.

막히면: spec과 이슈 본문을 다시 읽고, 그래도 모호하면 설계를 임의로 바꾸지 말고 질문을 정리해 사용자에게 보고한다.
