# Agent boundary

14:00 이후 아래 파일부터 구현합니다.

- `orchestrator.py`: 단일 입력에서 최종 결과까지의 실행 그래프
- `prompts.py` 또는 `prompts/*.md`: 버전 관리되는 지시문
- `guardrails.py`: 입력·출력 검증과 실패 처리

오케스트레이터는 UI나 특정 외부 서비스에 직접 의존하지 않게 유지합니다.

