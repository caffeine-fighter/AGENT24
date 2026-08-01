# AGENT:24 — Creative Agent

YAI × OpenAI AGENT:24 해커톤 Creative 트랙 4인 팀용 모노레포입니다.

> 대회 규정 보호 장치: 2026-08-01 14:00 KST 이전에는 커밋하지 않습니다. 첫 커밋 스크립트가 시작 시각을 검사하며, 모든 원격 작업은 그 이후에 수행합니다.

## 트랙 기준

- Creative Agent: 업무·학습·리서치 워크플로우 자동화 또는 글·음악·이미지 등 창작·생성
- OpenAI API/SDK 최소 1개 사용
- 입력 1회 후 에이전트가 판단, 도구 호출, 출력까지 독립 수행
- `tool_call` / `tool_result` 원시 이벤트를 두 번째 화면에 실시간 표시
- 발표 7분: 슬라이드 2분(최대 5장) + 라이브 데모 3분 + Surprise Task 2분
- 2026-08-02 08:00까지 2분 데모 영상, 발표 PDF, Daker 제출

공식 안내: <https://yonsei-yai-hackathon.netlify.app/>

## 권장 시작점

빠른 구현을 위해 Python + OpenAI Agents SDK + FastAPI + 얇은 웹 UI를 기본 경계로 잡았습니다. 실제 문제 정의와 제품 로직은 14:00 이후 팀이 결정해 구현합니다.

```text
src/agent24/agent/   에이전트 지시문·오케스트레이션
src/agent24/tools/   외부 도구와 함수 도구
src/agent24/api/     입력·출력 API와 SSE
src/agent24/events/  Raw API Stream 보존·전달
web/src/             데모 UI와 원시 이벤트 패널
tests/               unit / integration / evals
docs/                설계·프롬프트·데모·의사결정 기록
artifacts/           로컬 실행 산출물(기본적으로 Git 제외)
slides/              최대 5장 발표 자료
```

## 14:00 이후 첫 시작

```powershell
cd C:\Coding\Hackathon\2026YAI-AGENT24
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

`OPENAI_API_KEY`를 `.env`에 넣되 절대 커밋하지 마세요. 첫 커밋은 시작 시각 검사가 포함된 아래 명령으로 만듭니다.

```powershell
.\scripts\first-commit.ps1
```

## 4인 기본 분담

| 담당 | 역할 | 주 작업 영역 | 완료 기준 |
|---|---|---|---|
| `caffeine-fighter` (A) | Captain / MVP Integration | `web`, 전체 integration | 항상 실행 가능한 thin slice 유지 |
| `knowin-kyeong` (B) | Technical / Agent Intelligence Lead | `agent`, `evals`, architecture | 기술 방향, 핵심 난제, unseen-input 품질 책임 |
| `yoonhero` (C) | OpenAI Runtime / Tools | `api`, `tools`, `events` | 실제 OpenAI API와 Raw Stream 완주 |
| `rekhet` (D) | Product / UX / Demo Ops | `docs`, `slides`, QA | 명확한 수용 기준과 7분 발표 완성 |

세부 소유자는 [TEAM.md](TEAM.md)에 기록합니다.

## Definition of Done

- 새 입력 한 번으로 끝까지 실행된다.
- 도구 호출 이유와 순서가 설명 가능하다.
- Raw API 이벤트가 누락 없이 별도 패널과 JSONL에 남는다.
- 예상 밖 입력과 도구 실패 시 안전하게 복구한다.
- API 키·개인정보·대용량 산출물이 Git에 없다.
- 2분 영상과 3분 라이브 데모가 동일한 입력 계약을 사용한다.
