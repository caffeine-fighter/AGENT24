# Prompt change log

| Time (KST) | Version | Hypothesis | Change | Eval cases | Result |
|---|---|---|---|---|---|
| TBD | v0 | TBD | Initial prompt | TBD | TBD |
| 2026-08-01 | v1 | Lab Agent가 관찰 가능한 gym evidence 없이 진단하면 데모 신뢰도가 낮다 | `inspect_synthetic_gym`을 최종 답변 전에 항상 호출하고 관찰 필드와 검증 probe를 인용하도록 지시 | `tests/integration/test_api.py::test_live_run_uses_mocked_openai_client_and_preserves_raw_tool_items` | mocked Responses API에서 tool call → result → final output 통과 |

| 2026-08-01 | v1 (변경 없음) | 실험 선택을 LLM에 맡기면 같은 입력에서 다른 실험이 나와 replay와 감사가 불가능하다 | agent instruction 변경 없음. 이슈 #22의 P0 실험 선택은 `planner.select_p0_experiment`의 순수 결정적 정책으로 구현했고, 선택 근거는 `BehaviorProfile` 필드와 evidence ref에서만 나온다 | `tests/integration/test_planner_pipeline.py::test_normal_profile_selects_commit_then_timeout_and_reproduces_it`, `::test_ambiguous_profile_still_plans_and_names_its_unknown_basis`, `::test_unsupported_tooling_terminates_without_inventing_an_experiment` | 같은 profile·mission에서 항상 동일한 plan (`test_selection_is_deterministic`), README 문구를 바꿔도 선택 불변 (`test_selection_does_not_read_readme_or_tool_name_text`) |

| 2026-08-01 | v1 (변경 없음) | 보고서 문구를 모델이 생성하면 "실패를 못 찾았다"가 "안전하다"로 새어 나가고, 이는 프롬프트로 막을 수 없다 | agent instruction 변경 없음. 이슈 #23의 `report.py`는 순수 결정적 조립이며, terminal state는 `build_report`가 증거에서 유도한다. `bounded_summary`는 `compose_*_summary` 헬퍼가 생성하고 생성 텍스트를 받지 않는다. `no_failure_observed`에는 "안전성을 입증하지 않는다" 문장을 validator가 강제하고, 증거 없는 확정 문구는 스키마가 거부한다 | `tests/unit/test_report.py::test_no_failure_observed_without_the_disclaimer_is_rejected`, `::test_a_status_without_evidence_cannot_assert_a_verdict`, `::test_the_over_safe_patch_never_becomes_a_mitigation` | 5개 terminal state 전부 유효/위반 fixture 쌍 통과, 조립 결과 2회 동일 (`test_report_assembly_is_deterministic`) |

Store the runnable prompt beside the agent code. Use this log to explain prompt quality and iteration during judging.
