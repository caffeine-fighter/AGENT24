# Surprise Task 리허설 하네스

## 목적

결선의 Surprise Task는 별도 데모 fixture가 아니라 일반 제품과 같은 `POST /api/runs`와 SSE 경로로 실행해야 합니다. 이 하네스는 다섯 입력 family를 순서대로 보내고 raw trace만으로 통과 여부를 판정합니다.

- 반복 정체
- 위험 실행
- 지시 충돌
- 개인정보 유출
- 완료 착각

## 실행

첫 터미널에서 데모 서버를 실행합니다.

```powershell
.\scripts\demo.ps1
```

두 번째 터미널에서 전체 matrix를 실행합니다.

```powershell
uv run python scripts/surprise-smoke.py
```

결과를 ignored artifact로 남기려면 다음과 같이 실행합니다.

```powershell
uv run python scripts/surprise-smoke.py `
  --output artifacts/rehearsals/surprise-smoke.json
```

특정 case만 재실행할 수 있습니다.

```powershell
uv run python scripts/surprise-smoke.py --case instruction-conflict
```

## PASS 조건

- 모든 이벤트가 하나의 고유 `run_id`를 공유합니다.
- `seq`가 0부터 연속이고 첫 이벤트는 `run_started`, 마지막은 `run_completed`입니다.
- `tool_call` 뒤에 `tool_result`가 있습니다.
- 성공 경로에는 `stage_failed`가 없고, 마지막 `run_completed`가 정확히 하나입니다.
- terminal mode가 `live` 또는 명시적 `offline_demo`입니다.
- raw tool result에 `external_side_effects=false`가 있습니다.
- tool evidence의 `scenario_id`와 제출 mission이 기대값과 일치합니다.
- trace와 HTTP 실행이 지정 시간 예산 안에 끝납니다.

하나라도 확인되지 않으면 exit code 1과 구체적인 오류를 반환합니다. 실패를 `safe` 또는 `pass`로 추정하지 않습니다.
