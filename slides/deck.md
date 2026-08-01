# NIGHTMARE LAB — 5장 발표 원고

이 파일이 발표 PDF의 단일 copy source다. 슬라이드는 정확히 5장이고 총 2분이다.
한 장에 아래 `화면 문구`만 크게 넣고, `발표자 노트`는 PDF 본문에 넣지 않는다.

## 1 / 5 — 사고부터 보여주기 · 0:00–0:20

### 화면 문구

```text
케이크 1개를 부탁했다.

합성 세계는 2개 · ₩98,000을 만들었다.

NIGHTMARE LAB
BREAK IT BEFORE REALITY DOES
```

하단 고정: `SIMULATION · 실제 동작 없음`

### 화면 구성

- 왼쪽 1/3: “요청 1개 · 예산 ₩50,000”
- 오른쪽 2/3: UI의 보호 전 world card와 케이크 2개, 지갑 ₩402,000
- 첫 10초에는 팀명, 기술 stack, 긴 문제 설명을 넣지 않는다.

### 발표자 노트

“엄마 생일 케이크 하나를 5만원 이하로 부탁했습니다. 그런데 응답이 timeout되자
Agent가 같은 결제를 다시 시도했고, 합성 세계에는 두 개와 9만 8천 원의 피해가
남았습니다. 실제 결제는 없습니다.”

## 2 / 5 — 누구의 어떤 문제인가 · 0:20–0:42

### 화면 문구

```text
정상 예시가 통과해도,
현실의 timeout · 악성 입력 · 무한 retry는 남는다.

TARGET
side-effect Agent를 배포하는 개발팀

먼저 실패시키고, 기능을 살린 채 고친다.
```

### 화면 구성

- 중앙에 작은 네 아이콘만 사용: 지갑 / 메일 / 캘린더 / 파일
- “안전 점수”나 “성격 분석”이라는 단어는 사용하지 않는다.

### 발표자 노트

“우리 사용자는 돈, 메일, 일정, 파일을 다루는 Agent를 배포하는 팀입니다. 정상
예시만으로는 희귀한 실패를 보기 어렵습니다. 그래서 현실에 연결하기 전에 합성된
삶에서 사고를 능동적으로 찾습니다.”

## 3 / 5 — Agent가 하는 일 · 0:42–1:10

### 화면 문구

```text
ONE INPUT
GitHub repo + ref + mission

CLONE → CRASH → AUTOPSY → VACCINE → REPLAY

Source pinning      결정적 fault 선택
Raw API Stream      controller oracle
최소 보호책         same-seed + benign gate
```

하단 고정:

```text
SYNTHETIC ARCHETYPE
외부 저장소 코드는 실행하지 않음
```

### 화면 구성

- 파이프라인은 한 줄로 그리고 사용자 입력은 맨 왼쪽 한 번만 표시한다.
- `OBSERVED → HYPOTHESIS → PROPOSED → VERIFIED` 네 칸을 파이프라인 아래에 둔다.
- LLM은 판정자가 아니라 마지막 evidence 설명자라는 각주를 둔다.

### 발표자 노트

“저장소 ref를 commit으로 고정하고 allowlist manifest에서 관찰 가능한
BehaviorProfile을 만듭니다. Agent가 fault 하나를 선택해 실행하고, 숨겨진 world와
ledger를 controller oracle이 판정합니다. LLM은 결과를 설명하지만 합격 판정은 하지
않습니다. P0은 외부 저장소 코드를 실행하지 않습니다.”

## 4 / 5 — 측정된 before / after · 1:10–1:40

### 화면 문구

```text
MEASURED ON THE DETERMINISTIC FIXTURE

보호 전                    보호 후
2 charges                  1 charge
₩98,000                    ₩49,000
목표 위반                  목표 완료

3 / 3 same-seed reproductions
33 ordered events
benign control PASS
block-all policy REJECT
```

### 화면 구성

- 보호 전은 붉은색, 보호 후는 녹색으로 같은 크기에 나란히 둔다.
- ₩98,000 → ₩49,000과 2 → 1만 가장 크게 보이게 한다.
- `SIMULATION` badge를 수치 바로 위에 반복한다.

### 발표자 노트

“첫 결제는 commit됐지만 timeout처럼 보였습니다. idempotency key와 상태 조회를 넣자
같은 seed에서 한 건, 4만 9천 원으로 돌아왔고 원래 목표도 완료했습니다. 정상 작업도
통과했습니다. 결제를 전부 막는 정책은 케이크를 못 사기 때문에 명시적으로
거부합니다. 3/3과 33개 event는 오늘 실제 fixture rehearsal에서 측정했습니다.”

## 5 / 5 — 같은 경로로 Surprise · 1:40–2:00

### 화면 문구

```text
심사위원이 악몽을 고릅니다.

돈              위험한 송금
커뮤니케이션    이메일 지시 충돌
시간            캘린더 무한 반복
데이터          개인정보 유출

같은 POST /api/runs · 같은 Raw Stream · 중간 버튼 0

실패 미발견 ≠ 안전 인증
```

### 화면 구성

- 네 영역 카드를 2×2로 배치하고 중앙에서 같은 input box로 화살표를 모은다.
- 우측 하단에 라이브 화면으로 넘어가는 `LET'S CRASH IT`만 둔다.

### 발표자 노트

“이제 심사위원이 돈, 커뮤니케이션, 시간, 데이터 중 하나를 골라 주세요. 새 코드를
고르거나 숨은 runner를 쓰지 않습니다. 같은 API와 Raw Stream으로 한 번 입력해
끝까지 실행합니다. 여기서 실패가 안 보였다는 말도 안전 인증으로 바꾸지 않습니다.”

## 제작·검수 규칙

- 전체 슬라이드 수는 표지 포함 정확히 5장이다.
- 총 발표 시간은 `20 + 22 + 28 + 30 + 20 = 120초`다.
- 실제 UI screenshot은 rehearsal 입력과 같은 source/ref/mission에서 촬영한다.
- 합성 결제 화면을 실제 카드사·이메일·캘린더 화면처럼 꾸미지 않는다.
- 수치, event 개수, 상태명은 [`../docs/cake-collision-product-contract.md`](../docs/cake-collision-product-contract.md)와 다르게 적지 않는다.
- 최종 PDF/PPTX는 repository에 commit하지 않고 제출 링크와 hash만 `slides/README.md`에 기록한다.
