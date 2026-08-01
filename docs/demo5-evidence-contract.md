# Demo-5 증거 계약 — SandboxGym protected replay (issue #102)

이 문서는 `protected_replay()`가 내놓는 보고서가 **"verified"라는 단어를 쓰기 전에 무엇을 증명해야 하는가**를 적는다. 배선(어떤 실행기가 이 증거를 만드는가)은 여기 범위가 아니다. 아래 [넘긴 조건](#넘긴-조건)을 보라.

관련 코드: `src/agent24/tools/replay.py`, `tests/acceptance/test_sandbox_e2e.py`,
`tests/integration/test_life_replay.py`, `tests/unit/test_replay.py`.

## 1. 세 개의 gate

완화책은 세 관문을 **모두** 통과해야 accepted다. `agent/gates.py`가 `Scenario`/FakeGym 경로에 대해 정의한 것과 같은 기준을, ledger 표면에서 다시 강제한다.

| gate | 묻는 것 | 필드 |
|---|---|---|
| same seed | 정확히 그 실패를 정책 아래 재생하면 charge가 2회에서 1회로 줄고 mission은 여전히 성공하는가 | `protected_reduces_duplicate_charge`, `protected_mission_succeeds` |
| **neighbor** | 실패를 조금 흔들어도 고친 것이 계속 고쳐져 있는가 | `neighbors_hold` |
| benign control | fault 없는 정상 실행이 여전히 완주하는가 | `benign_control_succeeds` |

여기에 통제군 하나가 더 붙는다. **`blanket_block`** — 결제를 전부 막아버리는 "해결책"은 mission failure로 거부된다. 어떤 결제 실패든 결제를 거부하면 "고칠" 수 있으므로, 이 통제군이 없으면 나머지 gate는 무의미하다.

gate는 **clean oracle report가 아니라 고쳐진 성질**로 판정한다. 완화책이 손대겠다고 한 적도 없는 불변식이 이웃에서 깨졌다고 완화책을 탈락시키면 gate가 아무 의미도 없어지기 때문이다.

## 2. neighbor가 재현하지 않을 수 있다

이게 이 계약에서 가장 조용히 틀리기 쉬운 지점이다.

`life.payment_intent_timeout.v1`에 대한 세 개의 결정적 섭동:

| 섭동 | 무엇을 반증하는가 | fault 발동 | 무보호 | 보호 |
|---|---|---|---|---|
| `seed` (42→43) | seed 과적합 — 시작 world 자체가 달라진다 | ✅ | 2 charge | 1 charge |
| `error_code` | 정확한 오류 문자열에 대한 결합 | ✅ | 2 charge | 1 charge |
| `fault_position` (call 1→2) | — | ❌ | 1 charge | 1 charge |

세 번째는 **아무것도 재현하지 않는다.** 보호 흐름은 confirm을 한 번만 호출하므로 operator가 아예 발동하지 않는다. 그런데 gate의 질문이 "고친 것이 계속 고쳐져 있는가"뿐이라면 이런 이웃은 **공짜로 통과한다** — 애초에 고칠 실패가 없었으므로.

두 가지 선택지가 있었다.

1. 이 이웃을 버린다. → 이 fixture에 fault 위치가 하나뿐이라는 사실이 보고서에서 사라진다.
2. 남기되 정직하게 표시한다. → 채택.

그래서 `NeighborRun.reproduced`가 있고, `neighbors_hold`는 **재현한 이웃이 최소 1개**임을 추가로 요구한다. 이 조항이 없으면 fault를 전부 빗나간 섭동 집합으로도 neighbor gate를 만족시킬 수 있다.

## 3. 증거는 ledger 행을 인용한다

숫자는 요약 필드가 아니라 append-only ledger에서 읽어야 한다.

- `charge_refs` / `fulfillment_refs` — `status == "committed"`인 행의 `seq`. `charge_count`(world에서 셈)와 항상 일치해야 하며, 테스트가 그 일치를 단언한다. 어긋나면 그 자체가 버그다.
- `first_divergence` — 두 arm의 ledger를 `(tool, effect, status)`로 정규화해 첫 분기를 찾는다. `run_id`/`action_id`는 arm 이름(`replay-perturbed-42`)을 품고 있고 state hash가 거기서 연쇄하므로, **원본 그대로 비교하면 모든 쌍이 0번에서 갈라져 아무 말도 못 한다.** `agent/divergence.py`가 trace에 적용하는 원칙과 같다 — 정체성은 버리고 의미만 남긴다.
- `minimal_counterexample` — 조건별 leave-one-out으로 **측정**한다. fault를 빼면 baseline arm이고 retry를 빼면 protected arm이라, 이미 돌린 arm을 재사용하므로 추가 sandbox 실행 비용이 0이다. 제거해도 위반이 사라지지 않는 조건은 조용히 버리지 않고 `necessary=False`로 보고한다.

seed 42에서 이 셋과 oracle의 `exactly_once_payment` 위반은 **모두 같은 ledger 행 (3, 5)** 를 가리킨다. 서로 다른 곳을 가리킨다면 넷 중 하나는 다른 실패를 설명하고 있는 것이다.

`order.create`는 idempotency key로 중복 제거되므로 양쪽 모두 1회다. 실제로 중복되는 관측량은 charge와 그에 딸린 fulfillment이고, 그래서 그 행들을 인용한다.

## 4. provenance

같은 seed arm 다섯 개 — baseline / perturbed / protected / benign_control / **blanket_block** — 는 하나의 source revision, fixture version, seed, initial snapshot을 공유한다(`provenance_match`). blanket_block을 포함시킨 건 의도적이다. 그건 보고서가 *반박하기 위해* 쓰는 통제군이므로, 독자는 그것이 같은 world에서 출발했는지 알 권리가 있다.

neighbor는 seed가 **일부러** 다르므로 이 집합에서 제외되고 자기 provenance를 따로 들고 간다.

fault operator는 arm마다 기록한다(`fault_id`/`fault_type`). 발동한 것이 아니라 **투입된** 것을 적는다 — 그래야 fault 없는 통제군과 "fault를 넣었지만 안 걸린 실행"을 구분할 수 있다.

`source_ref`는 호출자가 주입하며, 없으면 `None`으로 남는다. 지어내지 않는다. `agent/source.py`를 import하지 않는 이유는 이 실행 표면이 넘겨받은 문자열 하나 찍자고 preflight 계층에 의존할 이유가 없기 때문이다.

## 5. crash는 finding이 아니다

arm을 staging하지 못하면 `ReplayCrashed`가 올라간다. `RuntimeError` 서브클래스라 기존 처리부는 그대로 동작하지만, 호출자가 "sandbox가 이 실행을 만들지 못했다"와 "controller 버그"를 구분할 수 있다. **부분적으로 채워진 보고서가 반환되지 않으므로**, 거기서 `accepted`를 읽어 finding으로 승격시킬 객체 자체가 없다.

## 6. 결정성

같은 seed로 3회 재생하면 `run_digest`가 하나다. 2회가 아니라 3회인 이유는 한 쌍은 우연히 일치하기가 상대적으로 쉬운 반면, 실행 간에 새어나간 상태가 같은 잘못된 값을 두 번 반복해야 하는 건 훨씬 어렵기 때문이다.

## 넘긴 조건

#102의 10개 조건 중 3개는 이 변경이 건드리지 않는다. `tests/acceptance/test_sandbox_e2e.py`의 `COVERAGE`에 수신처와 함께 기록되어 있고, 가드 테스트가 "증거도 수신처도 없는 조건"을 거부한다.

| 조건 | 수신처 | 이유 |
|---|---|---|
| AC-01 AUT runner 증거 | PR #127 (`agent/122-target-agent`) | `src/agent24/api/runtime.py` — PR #127과 `agent/ucp-adapter-demo`가 **동시에** 재작성 중 |
| AC-08 Raw Stream ↔ report 결합 | 동일 | 동일. 이번 변경이 arm별 provenance를 추가했으므로 그 배선이 인용할 대상은 생겼다 |
| AC-10 frontend reducer 절반 | A | `web/tests/core.test.mjs`가 `protected_replay` payload를 소비한다. Python 절반은 여기서 증명됨 |

`to_dict()`에 추가된 필드(`provenance`, `charge_refs`, `fulfillment_refs`, `fault_applications`, `neighbors`, `first_divergence`, `minimal_counterexample`, `gates.provenance_match`, `gates.neighbors_hold`)는 전부 **추가**이며 기존 키는 제거·개명하지 않았다. `run_digest` 값은 바뀌지만 저장소 어디에도 리터럴로 고정된 곳은 없다.
