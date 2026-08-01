# Prompt change log

| Time (KST) | Version | Hypothesis | Change | Eval cases | Result |
|---|---|---|---|---|---|
| TBD | v0 | TBD | Initial prompt | TBD | TBD |
| 2026-08-01 | v1 | Lab Agent가 관찰 가능한 gym evidence 없이 진단하면 데모 신뢰도가 낮다 | `inspect_synthetic_gym`을 최종 답변 전에 항상 호출하고 관찰 필드와 검증 probe를 인용하도록 지시 | `tests/integration/test_api.py::test_live_run_uses_mocked_openai_client_and_preserves_raw_tool_items` | mocked Responses API에서 tool call → result → final output 통과 |

Store the runnable prompt beside the agent code. Use this log to explain prompt quality and iteration during judging.
