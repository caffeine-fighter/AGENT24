# Prompt change log

| Time (KST) | Version | Hypothesis | Change | Eval cases | Result |
|---|---|---|---|---|---|
| TBD | v0 | TBD | Initial prompt | TBD | TBD |
| 2026-08-01 | v1 | Lab Agent가 관찰 가능한 gym evidence 없이 진단하면 데모 신뢰도가 낮다 | `inspect_synthetic_gym`을 최종 답변 전에 항상 호출하고 관찰 필드와 검증 probe를 인용하도록 지시 | `tests/integration/test_api.py::test_live_run_uses_mocked_openai_client_and_preserves_raw_tool_items` | mocked Responses API에서 tool call → result → final output 통과 |

| 2026-08-01 | v1 (변경 없음) | 실험 선택을 LLM에 맡기면 같은 입력에서 다른 실험이 나와 replay와 감사가 불가능하다 | agent instruction 변경 없음. 이슈 #22의 P0 실험 선택은 `planner.select_p0_experiment`의 순수 결정적 정책으로 구현했고, 선택 근거는 `BehaviorProfile` 필드와 evidence ref에서만 나온다 | `tests/integration/test_planner_pipeline.py::test_normal_profile_selects_commit_then_timeout_and_reproduces_it`, `::test_ambiguous_profile_still_plans_and_names_its_unknown_basis`, `::test_unsupported_tooling_terminates_without_inventing_an_experiment` | 같은 profile·mission에서 항상 동일한 plan (`test_selection_is_deterministic`), README 문구를 바꿔도 선택 불변 (`test_selection_does_not_read_readme_or_tool_name_text`) |

| 2026-08-01 | v1 (변경 없음) | 보고서 문구를 모델이 생성하면 "실패를 못 찾았다"가 "안전하다"로 새어 나가고, 이는 프롬프트로 막을 수 없다 | agent instruction 변경 없음. 이슈 #23의 `report.py`는 순수 결정적 조립이며, terminal state는 `build_report`가 증거에서 유도한다. `bounded_summary`는 `compose_*_summary` 헬퍼가 생성하고 생성 텍스트를 받지 않는다. `no_failure_observed`에는 "안전성을 입증하지 않는다" 문장을 validator가 강제하고, 증거 없는 확정 문구는 스키마가 거부한다 | `tests/unit/test_report.py::test_no_failure_observed_without_the_disclaimer_is_rejected`, `::test_a_status_without_evidence_cannot_assert_a_verdict`, `::test_the_over_safe_patch_never_becomes_a_mitigation` | 5개 terminal state 전부 유효/위반 fixture 쌍 통과, 조립 결과 2회 동일 (`test_report_assembly_is_deterministic`) |

| 2026-08-01 | v2 | source provenance와 synthetic archetype evidence를 함께 주면 모델이 이를 target code 실행 결과로 합칠 수 있다 | controller-owned `DIAGNOSTIC CONTEXT`의 scope를 명시하고 관찰·가설·제안·검증을 분리하며 제출 repository 실행을 주장하지 않도록 instruction 강화 | `tests/integration/test_api.py::test_live_target_passes_bounded_synthetic_evidence_to_openai`, `::test_diagnostic_failure_is_sanitized_and_falls_back` | mocked Responses API가 bounded context를 받고 tool call→final을 완주하며 source SHA·verified status를 보존; 내부 예외 문자열은 event/JSONL에 비노출 |
| 2026-08-01 | repository workflow (runtime prompt 변경 없음) | 완료된 작업을 commit하지 않거나 잘못된 branch에 commit하면 버전 복구와 작업 소유권 추적이 약해진다 | 매 commit 직전에 active branch와 staged diff를 확인하고, 완료된 non-decision 작업은 해당 작업 branch에 작은 commit으로 남긴다. Rekhet의 decision-relevant local 문서는 명시적 변경 지시가 없는 한 staging과 commit에서 제외한다. | N/A — repository collaboration rule; Lab Agent runtime behavior unchanged | Rule recorded in the durable Rekhet work plan; branch and staged-diff checks are required before every future commit |

Store the runnable prompt beside the agent code. Use this log to explain prompt quality and iteration during judging.
