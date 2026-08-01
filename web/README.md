# Demo web UI

기본 레이아웃은 두 패널입니다.

- Main: 단일 입력, 실행 상태, 최종 산출물
- Raw Stream: 타임스탬프가 붙은 원시 `tool_call` / `tool_result` JSON

SSE `data`는 `{run_id, seq, timestamp, type, payload, summary?}` envelope다. `payload`는 raw JSON으로 그대로 렌더링하고 `summary`만 화면용 라벨로 사용한다.

핵심 데모는 키보드 한 번으로 시작하고, 각 단계마다 사람이 승인 버튼을 누르는 UX는 피합니다. UI 프레임워크는 14:00 이후 팀이 선택합니다.
