# API boundary

최소 계약 후보:

- `POST /api/runs`: 한 번의 사용자 입력으로 실행 시작
- `GET /api/runs/{run_id}/events`: 결과와 Raw API Stream을 SSE로 전달
- `GET /health`: 데모 직전 의존성 상태 확인

최종 결과와 원시 이벤트는 같은 `run_id`로 연결합니다.

