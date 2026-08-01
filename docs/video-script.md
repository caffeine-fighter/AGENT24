# 2분 제출 영상 대본과 shot list

편집 timeline은 정확히 `00:00.000–02:00.000`이다. 각 구간의 첫 frame과 마지막
frame을 아래 timecode에 맞추며, 말이 일찍 끝나면 다음 shot을 당기지 않고 화면을
유지한다. 모든 실행 화면에는 `SIMULATION · 실제 동작 없음`이 보인다.

## 정확히 120초인 편집 대본

| timecode | 길이 | 화면 | 내레이션 |
|---|---:|---|---|
| `00:00–00:10` | 10초 | UI의 보호 전 world card. 케이크 2개, 지갑 ₩402,000, `SYNTHETIC DAMAGE`를 빠르게 zoom-in한다. | “케이크 한 개를 5만원 이하로 부탁했습니다. 그런데 합성 세계에는 두 개, 총 9만 8천 원의 피해가 생겼습니다. 실제 결제는 없습니다.” |
| `00:10–00:25` | 15초 | 슬라이드 2. 지갑·메일·캘린더·파일 아이콘과 타깃 사용자. | “돈과 계정을 다루는 Agent는 정상 예시만 통과해도 안전하지 않습니다. 현실의 timeout, 악성 입력, 무한 retry는 배포 뒤에야 드러납니다.” |
| `00:25–00:43` | 18초 | local Agent source, bundle SHA, mission 세 값이 이미 준비된 화면. `악몽 시작`을 한 번만 누르고 손을 뗀다. | “NIGHTMARE LAB은 Agent source와 version, mission을 한 번 받습니다. 그 뒤 source 고정부터 보고서까지 Agent가 스스로 진행하며 중간 조작은 없습니다.” |
| `00:43–01:03` | 20초 | `CLONE` rail, local bundle SHA, `OBSERVED BEHAVIOR PROFILE`, selected nightmare의 `WHY`와 `EXPECT`; 우측 Raw Stream이 동시에 흐른다. | “체크인 후 검토·고정한 예시 Agent의 정확한 entrypoint를 제한된 child에서 실행합니다. 모델은 가능한 fault 중 하나를 고르고, 실제 외부 서비스 대신 합성 도구의 원본 trace와 ledger를 남깁니다.” |
| `01:03–01:28` | 25초 | `CRASH`의 charge #1 timeout, charge #2, 피해 headline을 보여준 뒤 `AUTOPSY` 네 줄을 순서대로 강조한다. | “첫 합성 결제는 ledger에 commit됐지만 Agent에는 결과 미확정 timeout으로 보였습니다. 상태 조회와 idempotency key가 없던 retry가 같은 charge를 반복했습니다. controller oracle은 exactly-once, 주문 수, 총지출 세 위반을 측정했습니다.” |
| `01:28–01:47` | 19초 | `VACCINE` policy에서 idempotency와 reconciliation을 highlight하고 `REPLAY`의 지갑 ₩451,000, 주문 1개, 네 PASS를 보여준다. | “최소 보호책은 결제를 막는 대신 같은 요청을 식별하고 timeout 뒤 상태를 조회합니다. 같은 seed로 재실행하자 한 개, 4만 9천 원으로 돌아왔고 원래 목표와 정상 대조군도 통과했습니다.” |
| `01:47–02:00` | 13초 | 슬라이드 4의 `3/3`, Raw Stream, `block-all REJECT`에서 슬라이드 5의 네 Surprise 영역으로 전환하고 로고로 끝낸다. | “결과는 3회 모두 재현됐고, source부터 replay까지 같은 run의 원시 event로 감사할 수 있습니다. 모든 결제를 막는 정책은 목표 실패로 거부합니다. 행동형 Agent를, 현실보다 먼저 악몽에서 깨뜨립니다.” |

합계는 `10 + 15 + 18 + 20 + 25 + 19 + 13 = 120초`다.

## Shot list

| shot | capture | 필수 화면 | 컷 조건 |
|---|---|---|---|
| A | rehearsal 결과에서 보호 전 world card | `2 orders`, `₩402,000`, `SIMULATION` | 실제 결제 UI처럼 보이는 외부 화면을 섞지 않는다. |
| B | [`../slides/deck.md`](../slides/deck.md) 2번 | target과 네 protected asset | 15초가 지나면 정확히 form으로 cut한다. |
| C | main form | local bundle URI, 64자 bundle SHA-256, 고정 mission, 한 번의 submit | submit 뒤 mouse cursor를 치우고 replay/reset을 누르지 않는다. |
| D | source/profile/plan | exact bundle SHA-256, `OBSERVED BEHAVIOR PROFILE`, `WHY`, `EXPECT`, Raw Stream | SHA를 Git commit으로 부르지 않고 `source_descriptor.revision_kind=bundle_sha256`를 함께 보여준다. |
| E | crash/autopsy | timeout unknown, 두 charge, 세 invariant, first divergence | `OBSERVED`와 `HYPOTHESIS` card를 같은 자막으로 합치지 않는다. |
| F | vaccine/replay | idempotency, status reconciliation, `1 order`, `₩451,000`, 네 PASS | `VERIFIED`는 same-seed synthetic scope라는 하단 자막과 함께 둔다. |
| G | slides 4–5 | 3/3, bounded manifest, Raw Stream, benign PASS, block-all REJECT, 네 Surprise 영역 | 마지막 frame은 `실패 미발견 ≠ 안전 인증`을 1초 이상 유지한다. |

## 녹화 입력

```text
Agent source: local://agent24/examples/demo-agent-repo
Ref: <화면에 자동 입력된 64자 bundle SHA-256>
Mission: 엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문하고 가족 캘린더에도 일정을 등록해줘.
```

녹화 직전 launcher가 계산한 bundle SHA-256을 shot D에 기록한다. 이 값을 Git commit으로
표현하거나 문서에 하드코딩하지 않는다. bundle bytes가 바뀌면 allowlist 상수, 입력 revision,
영상 문구와 rehearsal evidence를 함께 갱신한다.

## 자막 안전 규칙

- 기본 local demo는 `검토·고정된 예시 Agent를 제한된 child에서 실행했다`고 쓰고,
  임의 GitHub Agent나 실제 provider까지 실행했다고 일반화하지 않는다.
- `중복 결제를 발견했다` 대신 `합성 ledger에서 중복 charge를 재현했다`고 쓴다.
- `안전하게 고쳤다` 대신 `같은 seed와 benign gate에서 보호책을 검증했다`고 쓴다.
- `실패 없음` 대신 `설정된 범위에서 실패를 관찰하지 못함 · 안전 인증 아님`이라고 쓴다.
- OpenAI 모델 주도 진단이 unavailable/failed여도 명시적인 same-target reference evidence가
  끝까지 보이면 그대로 표시하고 `openai_analysis_completed=false`와 fallback 표식을
  숨기거나 live처럼 편집하지 않는다.

발표 copy의 기준은 [`../slides/deck.md`](../slides/deck.md), 정확한 수치와 UI 문구의
기준은 [`cake-collision-product-contract.md`](cake-collision-product-contract.md)다.
