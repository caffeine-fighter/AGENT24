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

## 비공개 웹 데모 운영

- 데모 소스와 실행 이력은 이 비공개 저장소의 `web/`와 `site/`에서만 관리합니다.
- 별도 공개 저장소, GitHub Pages, ChatGPT Sites 주소는 공식 데모 경로로 사용하지 않습니다.
- OpenAI API를 사용하는 실제 경로는 로컬 또는 팀 전용 서버에서 실행하며, API key는 서버에만 둡니다.
- API가 없는 정적 화면은 `내장 예시`라고 표시하고 제출한 저장소를 분석한 결과로 주장하지 않습니다.
- 운영 체크리스트: [#50 A/배포: NIGHTMARE LAB 비공개 데모 운영](https://github.com/caffeine-fighter/AGENT24/issues/50)

## Product concept

- [NIGHTMARE LAB — Agent Failure Scientist](docs/nightmare-lab-concept.md): 다른 에이전트를 sandbox gym에서 자율적으로 실험해 최소 실패 조건을 진단하고 안전 패치를 재검증하는 제품 제안
- [예시 participant Agent repo](examples/demo-agent-repo/README.md): 결제 timeout 재시도를 선언한 독립형 repo fixture와 manifest/entrypoint
- [Sandbox Gym implementation spec](docs/gym.md): 생일 케이크 결제에서 시작하는 deterministic Life Gym, 단계별 Nightmare 확장, 구현 백로그와 완료 기준
- [Real-world Shopping Agent test cases](docs/real-world-shopping-agent-cases.md): 공개 GitHub 구매 Agent를 pinned source와 local Gym adapter로 검증하는 후보·시나리오 카탈로그
- [외부 Agent source·manifest 계약](docs/source-manifest-contract.md): GitHub commit provenance와 실행하지 않는 P0 manifest 입력 경계
- [외부 Agent 제품 계약](docs/external-agent-contract.md): one-input form, P0 지원 범위, BehaviorProfile 용어와 정확한 terminal 문구
- [케이크 충돌 제품 계약](docs/cake-collision-product-contract.md): 2개·₩98,000 → 1개·₩49,000 fixture, UI copy와 block-all 거부 gate
- [7분 데모 runbook](docs/demo-runbook.md): 5장·120초 slide, 3분 live, 2분 Surprise와 실제 rehearsal 기록
- [2분 제출 영상](docs/video-script.md): 정확히 120초인 대본과 shot list
- [Surprise missions](docs/surprise-missions.md): 돈·커뮤니케이션·시간·데이터 입력과 deterministic HTTP/SSE 판정
- [심사 Q&A](docs/judging-checklist.md): fixed runner, 승인, LLM judge, sandbox fidelity, overblocking 답변
- [5장 발표 원고](slides/deck.md): 최종 slide copy와 120초 발표자 노트

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

macOS/Linux에서 비공개 GitHub origin을 토큰 없이 리허설하려면 현재 checkout을
`local-demo` source로 명시해 전체 preflight → Gym → report 경로를 실행합니다.

```bash
uv run python scripts/demo-local.py
```

이 경로는 화면에 `local-demo`와 manifest/entrypoint bounded 경계를 표시하며, Agent
entrypoint는 분석용으로만 읽고 import/실행하지 않습니다. 실제 외부 side effect도 없습니다.

예시 participant Agent를 포함한 self-contained 데모를 실행하려면 다음 명령을 사용합니다.

```bash
uv run python scripts/demo-local.py --example-agent --port 8769
```

브라우저에서 <http://127.0.0.1:8769/index.html?demo=example-agent>를 열면
`examples/demo-agent-repo`를 `example-org/nightmare-cake-agent@demo-v1`라는
local fixture repo alias로 입력한 상태가 준비됩니다. 이 alias는 아직 공개 GitHub에
push된 저장소가 아니라 parent repo 안의 재현 가능한 fixture임을 화면과 문서에서
구분합니다.

실제 공개 GitHub 입력을 브라우저에서 검증하려면 `--live-github`를 사용합니다.

```bash
uv run python scripts/demo-local.py --live-github --port 8769
```

이 모드에서 `Upsonic/UCP-Agent@3f98ef0`의 exact reviewed source는
`ALLOWLISTED ADAPTER`로 표시되고 `complete_purchase`를 network-disabled local
replacement Gym에서만 측정합니다.

브라우저까지 자동으로 열려면 다음 옵션을 사용합니다.

```powershell
.\scripts\demo.ps1 -OpenBrowser
```

결선 Surprise Task와 같은 API 경로를 다섯 입력 family로 리허설합니다.

```powershell
uv run python scripts/surprise-smoke.py
```

판정 기준과 artifact 저장법은 [Surprise Task 리허설 문서](docs/surprise-smoke.md)에 있습니다.

## PR 검증

PR 전 canonical entrypoint는 `scripts/verify.ps1`이며 PowerShell 7에서는
`pwsh -File ./scripts/verify.ps1`로 실행합니다. 필수 도구를 찾지 못한
검사를 성공으로 간주하지 않으며, Python·web·hosted site·real-browser gate와 CI
artifact 비노출 기준은 [canonical verification 수용 계약](docs/verification.md)을
따릅니다. #70의 구현이 완료되기 전에는 현재 script의 성공만으로 전체 gate가
충족됐다고 주장하지 않습니다.

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
