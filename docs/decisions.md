# Decision log

| Time (KST) | Decision | Why | Owner | Revisit trigger |
|---|---|---|---|---|
| TBD | TBD | TBD | TBD | TBD |
| 2026-08-01 | OpenAI Agents SDK `Runner.run_streamed`와 `@function_tool`을 runtime의 단일 live 경로로 사용 | SDK가 모델 loop와 tool dispatch를 소유해 재구현 위험을 줄이고 raw semantic stream을 관찰할 수 있음 | C / runtime | 실제 gym stateful controller가 요구될 때 adapter contract 확장 |
| 2026-08-01 | 키 미설정·상류 timeout/error는 `offline_demo`로 명시하고 deterministic gym fallback을 실행 | 발표 환경의 네트워크·키 장애에서도 한 입력 데모를 끝까지 재현하고 실패 원인을 숨기지 않음 | C / runtime | 심사 환경에서 live-only 운영을 확정할 때 |
| 2026-08-01 | 결제 P0은 단일 `payment.charge` 대신 local-only PaymentIntent-style adapter를 기준으로 삼음 | 실제 hosted payment 연동의 idempotency, 상태 전이, webhook 재전달 경계를 재현하면서 외부 결제·개인정보는 차단 | C / gym | P1에서 provider별 adapter 차이를 별도 모델링할 때 |
| 2026-08-01 | SSE는 별도 `event:` 이름 없이 모든 envelope를 기본 `message`의 `data:`로 전달 | JSON 내부 `type`이 의미를 이미 담고 있으며, 단일 `onmessage` 경로가 예상하지 못한 타입도 Raw Stream에 손실 없이 보존함 | A / integration | 비브라우저 소비자가 SSE event-name 필터를 요구할 때 |
| 2026-08-01 | FastAPI가 `web/`을 catch-all로 마지막에 mount해 웹과 API를 단일 origin에서 제공 | 발표 현장의 CORS·포트·정적 서버 설정 실패를 제거하면서 API route 우선순위를 보존함 | A / integration | 웹을 별도 배포해야 할 때 |
| 2026-08-01 | Surprise 리허설은 별도 shortcut 없이 제품의 HTTP·SSE 경로와 raw trace gate를 사용 | Adaptability를 고정 답안이 아니라 동일 실행 경로의 순서·도구·종료·side-effect 증거로 검증하기 위함 | A / integration | live Lab pipeline의 event schema가 변경될 때 |
| 2026-08-01 | 외부 source는 GitHub URL을 full commit SHA로 resolve한 뒤 allowlisted JSON manifest만 읽음 | branch drift와 임의 repository code 실행을 막고 모든 진단 provenance를 재현 가능하게 유지 | C / input | private host·실행 가능한 adapter를 P1에서 별도 승인할 때 |
| 2026-08-01 | 결제 protected replay는 unknown confirm 뒤 새 intent를 만들지 않고 기존 intent를 retrieve함 | 같은 seed에서 duplicate charge를 제거하면서 정상 결제 control을 보존하는 최소 policy를 측정하기 위함 | C / replay | B의 generic oracle/report가 연결될 때 |
| 2026-08-01 | Research·Stock은 Life world에 필드를 추가하지 않고 read-only domain pack과 assessment 기반 oracle로 분리 | 문서 신뢰성 의미가 검증된 결제 ledger에 침투하는 것을 막고, 위험 fixture의 존재와 AUT의 실제 실패를 구분 | C / gym | 공통 provenance graph가 두 pack에서 반복 구현될 때 |
| 2026-08-01 | 외부 source는 metadata-only로 유지하고 manifest가 선택한 Life-v0 synthetic behavior archetype을 진단 | 임의 code 실행 없이 one-input E2E를 완주하면서 synthetic 측정을 실제 target 관찰로 과장하지 않기 위함 | A / integration | 승인된 실행 adapter와 sandbox contract가 추가될 때 |
| 2026-08-01 | `site/`에 Sites용 Worker 실행 표면을 추가하고 기존 FastAPI·정적 UI를 원본 구현으로 유지 | Python 서버를 실행하지 못하는 호스팅에서도 공유 링크·OpenAI 계획 호출·SSE 데모를 제공하되, 24시간 해커톤의 검증된 로컬 경로를 재작성하지 않음 | A / deployment | Python runtime을 직접 호스팅하는 프로덕션 환경이 준비될 때 |
| 2026-08-01 | hosted planner는 `commit_then_timeout` 한 변수만 허용하고 OpenAI에는 선택 이유·기대 증거만 맡김 | 모델의 적응성을 노출하면서 scenario·oracle·패치·판정의 결정성을 controller가 계속 소유하게 함 | A / deployment | hosted domain pack이 Research·Stock·Adhoc까지 확장될 때 |
| 2026-08-01 | Sites build가 삽입한 Git commit은 배포 provenance로만 표시하고 submitted-source pin에 대체 사용하지 않음 | D1의 공개 GitHub preflight와 fixture fallback을 분리하고 private self-target 우회를 제거 | A / deployment | Sites가 D1 공개 source·manifest preflight를 직접 제공할 때 |
| 2026-08-01 | 외부-Agent P0는 public GitHub의 `agent24.manifest.v1` metadata만 읽고 submitted code나 private credential을 사용하지 않으며, legacy string은 structured target migration bridge로만 유지 | 재현 가능한 provenance와 one-submission replacement를 제공하면서 credential·임의 code 실행 위험과 실제 target 분석 과장을 차단 | D / product contract | 승인된 execution adapter 또는 private-source auth가 별도 설계되거나 D3/A4 migration이 완료될 때 |

Keep entries short. Record choices that affect architecture, model/tool selection, scope, cost, privacy, or the demo path.
