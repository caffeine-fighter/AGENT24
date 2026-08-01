# Raw event boundary

`tool_call`과 `tool_result`를 의미 변환 없이 보존하고 다음 두 소비자에게 동시에 전달합니다.

1. 데모용 두 번째 화면의 실시간 JSON 패널
2. `artifacts/raw-streams/<run_id>.jsonl` 로컬 로그

표시용 요약이 필요하면 원본과 별도 필드/채널로 만듭니다. 비밀값과 개인정보는 도구 입력 단계에서 받지 않는 것을 우선합니다.

