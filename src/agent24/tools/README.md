# Tool boundary

이 디렉터리는 두 종류의 경계를 제공한다.

## Scenario inspector

기존 `inspect_synthetic_gym(query: str) -> str`는 짧은 데모와 offline fallback을 위한
읽기 전용 inspector다.

- 입력: 실패 상황을 설명하는 한 문장(최대 240자)
- 출력: `gym`, `scenario_id`, `failure_signal`, `probe`, reversible state를 담은 deterministic JSON
- 외부 서비스/side effect: 없음

## Stateful sandbox

Issue #5의 backend는 `load_fixture()`로 만든 `SandboxGym`이다. AUT에는 실제 서비스 대신
다음 function-tool schema를 제공한다.

| 개념적 tool | side effect | 합성 상태 |
|---|---:|---|
| `catalog.search` | 아니오 | catalog/web |
| `payment.charge` (legacy) | 예 | wallet, orders, ledger |
| `payment.status` (legacy) | 아니오 | ledger 조회 |
| `payment_intent.create` | 예 | PaymentIntent object, order status, ledger |
| `payment_intent.confirm` | 예 | charge, wallet, order, webhook, ledger |
| `payment_intent.retrieve` | 아니오 | PaymentIntent state 조회 |
| `order.create` | 예 | orders, ledger |
| `webhook.deliver` | 예 | delivery count, ledger |
| `order.fulfill` | 예 | fulfillment, order status, ledger |
| `web.fetch`, `web.search` | 아니오 | untrusted web |
| `email.send` | 예 | inbox, ledger |
| `calendar.create` | 예 | calendar, ledger |
| `file.write` | 예 | files, ledger |

Agents SDK wrapper는 이름 제약 때문에 `payment_charge`처럼 underscore를 사용하지만,
fixture·ledger·진단 결과에는 개념적 dotted tool 이름을 유지한다.
`payment_intent_create`의 `metadata`는 provider처럼 임의의 string map을 허용하므로
Agents SDK strict schema를 해당 wrapper에서만 해제했고, 로컬 domain method와 ledger는
여전히 typed payload를 사용한다.

### Determinism contract

- `fixture_id + seed`가 같은 경우 initial snapshot hash가 같다.
- `WorldSnapshot`은 canonical JSON과 hash를 함께 보유하며 restore 시 hash를 다시 검증한다.
- 모든 변경은 `SideEffectLedger`의 immutable entry를 남긴다.
- `commit_then_timeout`은 state/ledger commit 뒤 AUT에 `status: unknown`을 반환한다.
- 같은 `idempotency_key`로 재호출하면 새 side effect 없이 기존 payment/order를 반환한다.
- `payment_intent.confirm`은 timeout이 와도 charge가 이미 생성될 수 있으므로
  `payment_intent.retrieve`가 reconciliation 경로다.
- `requires_action`과 `processing`은 성공이 아니다. webhook/event와 현재 provider object를
  확인하기 전에는 `order.fulfill`을 호출하면 안 된다.
- `webhook.deliver`는 duplicate delivery를 만들 수 있고, `order.fulfill`은 event ID 또는
  idempotency key로 중복을 막아야 한다.
- `reset()`은 world, fault call count, ledger를 새 run으로 되돌린다.

현재 제공 fixture:

- `life.cake_collision.v1`
- `life.web_injection.v1`
- `life.empty_search.v1`
- `life.payment_intent_timeout.v1`
- `life.webhook_duplicate.v1`
- `life.webhook_out_of_order.v1`

실제 결제, 메일, 파일 시스템, 웹 네트워크는 호출하지 않는다. UI와 FastAPI는 이 계층의
state와 evidence를 표시하는 소비자이며 sandbox backend를 직접 조작하지 않는다.

## Read-only domain packs

`ResearchGym`, `StockGym`, `AdhocGym`은 Life world를 확장하지 않는 별도 read-only
경계다. Research/Stock tool은 synthetic 문서·공시·뉴스·entity metadata만 반환하고,
AUT assessment를 받는 controller-side `diagnose()`가 evidence-bounded finding을 만든다.
Adhoc registry는 allowlisted scenario와 최대 8회 tool-call budget만 허용한다.

fixture, tool, assessment, oracle 계약은 [domain-gym-packs.md](../../../docs/domain-gym-packs.md)에
정리되어 있다.

## Provider-like flow

새 결제 시연은 다음 순서로 구성한다.

```text
order.create
→ payment_intent.create (cart idempotency key)
→ payment_intent.confirm (confirm idempotency key)
→ payment_intent.retrieve (응답이 unknown인 경우)
→ webhook.deliver
→ order.fulfill (event ID dedupe)
```

이 흐름은 단일 `payment.charge` 호출보다 실제 hosted payment API의 실패 경계를 잘
드러낸다. 같은 PaymentIntent를 재조회하면 한 번의 charge를 복구할 수 있지만, 새 intent를
만들어 재시도하면 ledger에서 두 charge와 두 번의 wallet 차감이 관찰된다. 모든 ID와 상태는
synthetic fixture에서 결정되며 네트워크 요청은 없다.
