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

## 배포된 웹 데모

- [NIGHTMARE LAB production](https://nightmare-lab-agent24.conceivability.chatgpt.site) — 현재 owner-only Sites 링크
- 한 입력으로 GitHub ref 확인 → OpenAI 실험 근거 생성 → `CLONE → CRASH → AUTOPSY → VACCINE → REPLAY`를 SSE Raw Stream으로 표시
- hosted secret이 없거나 외부 호출이 실패하면 화면에 `offline_demo`를 명시하고 동일한 합성 fallback으로 완주
- 운영 체크리스트: [#50 A/배포: NIGHTMARE LAB 웹 데모 링크 운영](https://github.com/caffeine-fighter/AGENT24/issues/50)

## Product concept

- [NIGHTMARE LAB — Agent Failure Scientist](docs/nightmare-lab-concept.md): 다른 에이전트를 sandbox gym에서 자율적으로 실험해 최소 실패 조건을 진단하고 안전 패치를 재검증하는 제품 제안
- [Sandbox Gym implementation spec](docs/gym.md): 생일 케이크 결제에서 시작하는 deterministic Life Gym, 단계별 Nightmare 확장, 구현 백로그와 완료 기준
- [Real-world Shopping Agent test cases](docs/real-world-shopping-agent-cases.md): 공개 GitHub 구매 Agent를 pinned source와 local Gym adapter로 검증하는 후보·시나리오 카탈로그
- [외부 Agent source·manifest 계약](docs/source-manifest-contract.md): GitHub commit provenance와 실행하지 않는 P0 manifest 입력 경계

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

## 데모 실행

의존성을 한 번 설치한 뒤 웹과 API를 같은 origin에서 실행합니다.

```powershell
uv sync --extra dev
.\scripts\demo.ps1
```

<http://127.0.0.1:8000>을 열면 됩니다. 로컬 `.env`에 `OPENAI_API_KEY`가 있으면 live mode, 없으면 화면과 Raw Stream에 명시된 deterministic `offline_demo` mode로 실행됩니다.

브라우저까지 자동으로 열려면 다음 옵션을 사용합니다.

```powershell
.\scripts\demo.ps1 -OpenBrowser
```

결선 Surprise Task와 같은 API 경로를 다섯 입력 family로 리허설합니다.

```powershell
uv run python scripts/surprise-smoke.py
```

판정 기준과 artifact 저장법은 [Surprise Task 리허설 문서](docs/surprise-smoke.md)에 있습니다.

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
