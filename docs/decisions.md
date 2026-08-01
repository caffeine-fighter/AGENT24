# Decision log

| Time (KST) | Decision | Why | Owner | Revisit trigger |
|---|---|---|---|---|
| TBD | TBD | TBD | TBD | TBD |
| 2026-08-01 | OpenAI Agents SDK `Runner.run_streamed`와 `@function_tool`을 runtime의 단일 live 경로로 사용 | SDK가 모델 loop와 tool dispatch를 소유해 재구현 위험을 줄이고 raw semantic stream을 관찰할 수 있음 | C / runtime | 실제 gym stateful controller가 요구될 때 adapter contract 확장 |
| 2026-08-01 | 키 미설정·상류 timeout/error는 `offline_demo`로 명시하고 deterministic gym fallback을 실행 | 발표 환경의 네트워크·키 장애에서도 한 입력 데모를 끝까지 재현하고 실패 원인을 숨기지 않음 | C / runtime | 심사 환경에서 live-only 운영을 확정할 때 |
| 2026-08-01 | SSE는 별도 `event:` 이름 없이 모든 envelope를 기본 `message`의 `data:`로 전달 | JSON 내부 `type`이 의미를 이미 담고 있으며, 단일 `onmessage` 경로가 예상하지 못한 타입도 Raw Stream에 손실 없이 보존함 | A / integration | 비브라우저 소비자가 SSE event-name 필터를 요구할 때 |
| 2026-08-01 | FastAPI가 `web/`을 catch-all로 마지막에 mount해 웹과 API를 단일 origin에서 제공 | 발표 현장의 CORS·포트·정적 서버 설정 실패를 제거하면서 API route 우선순위를 보존함 | A / integration | 웹을 별도 배포해야 할 때 |
| 2026-08-01 | Surprise 리허설은 별도 shortcut 없이 제품의 HTTP·SSE 경로와 raw trace gate를 사용 | Adaptability를 고정 답안이 아니라 동일 실행 경로의 순서·도구·종료·side-effect 증거로 검증하기 위함 | A / integration | live Lab pipeline의 event schema가 변경될 때 |

Keep entries short. Record choices that affect architecture, model/tool selection, scope, cost, privacy, or the demo path.
