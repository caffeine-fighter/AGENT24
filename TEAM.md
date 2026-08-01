# Team ownership

팀원 매핑은 다음과 같습니다.

- A: `caffeine-fighter`
- B: `knowin-kyeong`
- C: `yoonhero`
- D: `rekhet`

## 역할 배분

| ID | 프로필 | 역할 | 최종 책임 영역 | 주 작업 경로 | 개발 보조 도구 |
|---|---|---|---|---|---|
| A · `caffeine-fighter` | MVP 해커톤 경험 최다, Hermes Agent·Codex 활용, GPT Pro | **Captain / MVP Integration Lead** | 범위·우선순위, 전체 통합, 웹 thin slice, merge·release 판단, 라이브 데모 지휘 | `web/`, 전체 integration, `docs/demo-runbook.md` | Codex, Hermes, GPT |
| B · `knowin-kyeong` | 팀 내 최고 기술 역량, ML 대회 최상위권, 폭넓은 언어·AI/ML·백엔드 경험, Claude 100x | **Technical Lead / Agent Intelligence Lead** | 전체 기술 설계, 가장 어려운 불확실성 해결, 에이전트 그래프·프롬프트·eval, 핵심 코드 리뷰 | `src/agent24/agent/`, `tests/evals/`, `docs/architecture.md`, `docs/prompt-log.md` | Claude |
| C · `yoonhero` | 해커톤 경험, ML 대회 강점, GPT Pro | **OpenAI Runtime / Tools Lead** | OpenAI API·Agents SDK, 함수 도구, FastAPI, Raw API Stream, 오류·재시도 | `src/agent24/api/`, `src/agent24/tools/`, `src/agent24/events/` | GPT |
| D · `rekhet` | 기획 강점, 구현 역량은 상대적으로 제한적, Claude 보유 | **Product / UX / Demo Ops Lead** | 문제 정의, 사용자 시나리오, 요구사항·이슈 관리, 블랙박스 QA, 5장 슬라이드와 2분 영상 | `docs/`, `slides/`, GitHub Issues, UI 카피·수용 기준 | Claude |

## 왜 이렇게 나누는가

- A는 가장 비싼 자원인 **시간과 통합 리스크**를 관리합니다. 한 기능을 깊게 파기보다 네 파트를 계속 연결해 동작하는 MVP를 유지합니다.
- B는 공개 프로필에서 Python·C/C++·Java·Kotlin, PyTorch·LLM·RL, 백엔드 경험과 ML 대회 상위권 실적이 확인됩니다. 따라서 단일 모듈 담당이 아니라 **기술 방향과 가장 어려운 문제의 최종 책임자**로 둡니다.
- B와 C는 모두 ML에 강하지만, B는 **기술 설계·행동 품질·실험 판단**, C는 **실제 OpenAI 런타임과 도구 실행**을 분리 소유해 병렬로 일합니다.
- D는 단순 서포트가 아니라 **무엇을 만들지, 어떻게 검증하고 설득할지**의 오너입니다. 개발자는 D가 작성한 수용 기준을 통과해야 합니다.

B의 공개 프로필 근거: <https://knowin-kyeong.github.io/>

## 의사결정권

| 결정 | DRI | 반드시 협의할 사람 |
|---|---|---|
| 타깃 사용자·핵심 문제·성공 지표 | D | A |
| MVP 범위·기능 삭제·시간 배분 | A | B, C, D |
| 전체 기술 아키텍처·인터페이스·기술 부채 예외 | B | A, C |
| Agent 구조·프롬프트·eval 통과 기준 | B | C |
| OpenAI API/SDK·도구·이벤트 스트림 구현 | C | B |
| 데모 시나리오·슬라이드·영상 | D | A |
| PR merge·release 후보 지정 | A | 해당 영역 오너 |

## OpenAI 필수 조건

- Claude, GPT Pro, Codex, Hermes는 **개발 보조 수단**입니다.
- 제출 제품의 실제 실행 경로는 반드시 `OPENAI_API_KEY`를 사용하는 OpenAI API/SDK를 호출해야 합니다.
- ChatGPT Plus/Pro 구독과 OpenAI API 크레딧은 별개이므로, C가 킥오프 즉시 API 키·결제/크레딧·rate limit을 확인합니다.
- C는 원시 `tool_call` / `tool_result` 이벤트를 보존하고, A는 이를 데모의 두 번째 화면에 표시합니다.

## AI 코딩 의존성 안전장치

- A가 Codex/Hermes로 만든 통합 코드는 영역에 따라 B 또는 C가 실행 경로를 검토합니다.
- B와 C의 프롬프트·도구 변경은 서로 교차 리뷰합니다.
- B가 작성한 핵심 코드도 C가 재현 실행해야 하며, B의 승인만으로 merge하지 않습니다.
- AI가 생성한 코드는 작성자가 직접 실행하고 실패 원인을 설명할 수 있어야 merge합니다.
- D는 구현을 보지 않고 사용자 입력만으로 블랙박스 검수합니다.
- 핵심 데모 경로에는 담당자 외 한 명이 실행 가능한 runbook을 둡니다.

## B를 병목으로 만들지 않는 규칙

- B는 모든 코드를 직접 작성하지 않습니다. 아키텍처·인터페이스·통과 기준을 먼저 정하고 구현은 C와 A에게 위임합니다.
- B의 주력 시간은 가장 불확실한 agent behavior, 알고리즘, eval 문제에 씁니다.
- API plumbing, Raw Stream, UI, 슬라이드는 각각 C, A, D가 독립적으로 끝냅니다.
- B가 20분 이상 막힌 구현을 직접 붙잡고 있으면 A가 scope 축소 또는 담당 재배치를 결정합니다.

## 첫 8시간 실행 순서

### 14:00–14:20 — 전원

- D가 타깃 사용자, 문제, 성공 지표를 한 문장씩 확정합니다.
- A가 3분 데모에서 보여줄 단 하나의 핵심 흐름과 버릴 기능을 정합니다.
- B가 전체 agent pipeline과 모듈 인터페이스를 확정하고, C와 OpenAI API 호출·도구 이벤트 계약을 합의합니다.

### 14:20–16:00 — 병렬 thin slice

- A: 입력창 → 실행 상태 → 결과/Raw Stream 2패널 UI 골격
- B: 가장 위험한 기술 가설 검증, 최소 agent instruction, 도구 선택 기준, 정상·모호·실패 eval 각 1개
- C: 실제 OpenAI API 호출 → 도구 1개 → 원시 이벤트 → 최종 출력 경로
- D: 사용자 시나리오, 수용 기준, 슬라이드 1–3장 초안, 테스트 입력 10개

**16:00 통합 게이트:** 입력 1회 → OpenAI 호출 → 도구 호출 → Raw Stream → 결과 표시가 한 번은 끝까지 돌아가야 합니다.

### 16:00–19:00 — 가치 구현

- A는 네 영역을 통합하고 막힌 사람에게 즉시 붙습니다.
- B는 실제 아이디어의 에이전트 행동과 eval을 확장합니다.
- C는 핵심 도구, timeout, retry, fallback을 완성합니다.
- D는 사용자 관점 QA와 데모 문구를 다듬고 결과의 임팩트를 수치화합니다.

**19:00 기능 동결 후보:** 이후 새 기능은 A와 D가 데모 가치를 함께 승인한 경우에만 추가합니다.

### 19:00–22:00 — 실패 대응

- 네트워크 오류, 빈 결과, 잘못된 입력, 긴 입력을 전원이 교차 테스트합니다.
- B가 처음 보는 Surprise Task 세트를 만들고, D가 블랙박스로 실행합니다.
- A와 C가 데모 경로의 로컬 fallback을 확인합니다.

## Shared checkpoints

- 14:20 문제 정의와 성공 지표 확정
- 16:00 첫 end-to-end thin slice
- 19:00 기능 동결 후보 결정
- 22:00 unseen input·도구 실패 점검
- 00:00 제품 범위 동결
- 04:00 데모 경로 동결, 영상·슬라이드 제작 시작
- 07:00 최종 제출물 검증
