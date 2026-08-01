# Worker B 킥오프 프롬프트 — Wave 3: 이슈 #23 FindingReport와 정직한 종료 상태

> 아래 블록 전체를 Opus 5 세션에 붙여넣는다. main 최신(`git pull`)에서 시작한다.
> 브랜치: `agent/finding-report`

---

너는 NIGHTMARE LAB 해커톤 프로젝트의 구현 워커다. Wave 2까지 merge된 상태다: 동결 계약(`models.py`, `protocols.py`, `fakes.py`, `capabilities.py`), 네가 만든 `profile.py`/`planner.py`, 그리고 다른 워커가 만든 `oracle.py`/`divergence.py`/`minimize.py`/`gates.py`. **기존 파일은 전부 읽기 전용으로 소비한다** — 이번 임무는 새 파일 하나로 완결된다.

## 필독 (순서대로)

1. `gh issue view 23` — 담당 이슈. 작업 항목과 완료 조건이 이번 임무의 전부다.
2. `docs/nightmare-lab-concept.md` — "진단 보고서 계약" 섹션 (Observed/Diagnosis hypothesis/Proposed patch/Verified/Residual risk 분리 예시)
3. `src/agent24/agent/models.py` — 재사용할 동결 모델: `OracleReport`, `Violation`, `Hypothesis`, `AntibodyPatch`, `PatchVerification`, `MinimizationResult`, `Divergence`, `StopDecision`, `Finding`, `canonical_json`, `run_digest`
4. `src/agent24/agent/oracle.py`(`evaluate`, `canonical_report`), `gates.py`(`verify_patch`), `planner.py`(`select_p0_experiment`의 StopDecision), `minimize.py` — 보고서가 포장할 상류 산출물의 실제 API
5. `docs/plans/issue-3-4-spec.md` §2 — 계약 설계 배경

## 임무: 이슈 #23 — 새 파일 `src/agent24/agent/report.py` + `tests/unit/test_report.py`

실험 결과를 관찰·진단 가설·패치 제안·검증 결과로 분리한 machine-readable 보고서.

### 스키마

- `FindingReportStatus(StrEnum)`: `measured_failure`, `verified_mitigation`, `no_failure_observed`, `unsupported`, `budget_exhausted` — 5개 terminal state
- `ArtifactRef`: raw 산출물 참조 (`kind: Literal["run_digest", "jsonl", "snapshot"]`, `ref: str`) — UI 요약과 원본 증거를 분리
- `FindingReport` (pydantic v2, frozen 유지):
  - `status: FindingReportStatus`
  - `observed: OracleReport | None` — 측정 사실만
  - `diagnosis_hypothesis: Hypothesis | None` — 명시적으로 가설
  - `proposed_patch: AntibodyPatch | None`
  - `verification: PatchVerification | None` — 실제 replay 검증 결과
  - `reproduction_count: int`, `reproduction_total: int` (예: 3/3)
  - `evidence: list[ArtifactRef]`, `residual_risk: list[str]`
  - `bounded_summary: str` — UI용, **최대 길이 제한(예: 280자)을 validator로 강제**
- 상태별 성립 조건을 **pydantic model validator로 강제** (이슈 완료 조건 "evidence 없는 취약성 확정 문구를 schema가 거부"):
  - `measured_failure` → `observed.violations`가 비어있지 않고 각 Violation에 ledger/trace/state 증거 존재, `reproduction_count >= 1`
  - `verified_mitigation` → `verification.accepted == True` **그리고** `observed` 위반 증거 동반 (검증 없는 patch 제안을 mitigation으로 표기하면 ValidationError)
  - `proposed_patch`가 있어도 `verification`이 없거나 `accepted=False`면 status는 `measured_failure`에 머문다 — **patch 제안과 replay 검증을 혼동 금지**
  - `no_failure_observed` → violations 없음 + `bounded_summary`에 안전 인증이 아님이 명시되도록 결정적 문구 생성 헬퍼 제공 (`compose_no_failure_summary(experiments_run, families)` 등). "안전함이 입증됨" 류 확정 문구는 validator가 거부
  - `unsupported` / `budget_exhausted` → `StopDecision.reason`(`unsupported_input`/`budget_exhausted`)에서 매핑하는 factory 제공, findings 관련 필드는 비어 있어야 함
- 조립 factory: `build_report(...)` — oracle/minimize/gates/planner 산출물을 받아 올바른 status의 FindingReport를 만드는 유일한 공식 경로 (상류 API를 직접 재해석하지 않는다)
- serialization: `canonical_json` 기반 canonical dump 헬퍼

### 테스트 (`tests/unit/test_report.py`, 네트워크 0·결정적)

- terminal state 5종 각각: 유효 fixture 1개 + **위반 fixture 1개(ValidationError 확인)**
  - 특히: 증거 없는 measured_failure 거부 / verification 없는 verified_mitigation 거부 / bounded_summary 길이 초과 거부 / "안전 인증" 문구 거부
- FakeGym 기반 실전 fixture: `retry_happy`+`commit_then_timeout` 실행 → `oracle.evaluate` → `gates.verify_patch` 산출물로 `build_report` → `verified_mitigation` 성립, dump 결정성(2회 동일)
- `tests/evals/cases.yaml`에 `report-*` id로 behavior eval 항목 append (각 terminal state 최소 1개, pytest node id를 command로). **기존 항목은 절대 수정 금지** — 파일 끝에 추가만.

## 절대 규칙

- 수정 가능: `src/agent24/agent/report.py`(신규), `tests/unit/test_report.py`(신규), `tests/evals/cases.yaml`(append만), 필요 시 `docs/prompt-log.md`(한 줄 기록). **그 외 전부 금지** — `oracle.py`/`gates.py`/`minimize.py`/`divergence.py`는 다른 워커 산출물, `models.py` 등 계약 파일은 동결. 상류 API가 보고서 조립에 부족하면 수정하지 말고 사용자에게 보고.
- `src/agent24/agent/` 안에서 `openai`/`agents` import 금지. 모든 테스트는 네트워크 없이 결정적.
- 커밋: Conventional Commits(`feat(agent): ...`, `test(agent): ...`), 모듈+테스트 같은 커밋. PR 전 `.\scripts\verify.ps1` 통과.
- PR 본문: 이슈 #23 완료 조건 체크리스트 + 각 항목 증거 테스트 이름, `Closes #23`. 그리고 A(웹 #25)와 다음 wave(loop 조립)가 이 스키마를 소비하므로 **FindingReport 필드 요약을 PR 본문에 표로 첨부**한다.

막히면: 이슈 본문과 concept 문서의 보고서 계약을 다시 읽고, 그래도 모호하면 설계를 임의로 바꾸지 말고 질문을 정리해 사용자에게 보고한다.
