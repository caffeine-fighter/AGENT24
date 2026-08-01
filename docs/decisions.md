# Decision log

| Time (KST) | Decision | Why | Owner | Revisit trigger |
|---|---|---|---|---|
| TBD | TBD | TBD | TBD | TBD |
| 2026-08-01 | OpenAI Agents SDK `Runner.run_streamed`와 `@function_tool`을 runtime의 단일 live 경로로 사용 | SDK가 모델 loop와 tool dispatch를 소유해 재구현 위험을 줄이고 raw semantic stream을 관찰할 수 있음 | C / runtime | 실제 gym stateful controller가 요구될 때 adapter contract 확장 |
| 2026-08-01 | 키 미설정·상류 timeout/error는 `offline_demo`로 명시하고 deterministic gym fallback을 실행 | 발표 환경의 네트워크·키 장애에서도 한 입력 데모를 끝까지 재현하고 실패 원인을 숨기지 않음 | C / runtime | 심사 환경에서 live-only 운영을 확정할 때 |

Keep entries short. Record choices that affect architecture, model/tool selection, scope, cost, privacy, or the demo path.
