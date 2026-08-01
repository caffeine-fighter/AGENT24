# Canonical verification 수용 계약

상태: **D 수용 기준 동결, A 구현 대기**

추적: [#70](https://github.com/caffeine-fighter/AGENT24/issues/70)

이 문서는 로컬과 CI가 공유할 검증 계약을 정한다. 현재 `scripts/verify.ps1`이 이
계약을 모두 구현했다는 뜻이 아니다. #70은 아래 gate가 실제 clean checkout과 CI에서
통과하고 증거가 남을 때까지 열린 상태로 유지한다.

## 책임 경계

- D는 필수 gate, 실패 안내, browser evidence와 artifact 비노출 기준을 소유한다.
- A는 `scripts/verify.ps1`, CI 연결, `web/`·`site/` drift 검출과 real-browser smoke를
  구현한다.
- 구현 PR은 이 문서를 바꾸기보다 이 문서의 각 gate를 충족했다는 focused check를
  제시한다. 제품 또는 보안 경계를 바꿔야 한다면 D와 먼저 합의한다.

## 하나의 진입점

저장소의 canonical entrypoint는 `scripts/verify.ps1` 하나다.

```powershell
./scripts/verify.ps1
```

Windows 외 환경과 CI에서는 같은 파일을 PowerShell 7로 실행한다.

```bash
pwsh -File ./scripts/verify.ps1
```

clean checkout은 tracked source와 lockfile만 존재하고 `.venv/`, `site/node_modules/`,
build output, browser cache, `.env`가 없는 상태를 뜻한다. dependency cache는 속도만
높일 수 있으며 cache 유무에 따라 gate나 결과가 달라지면 안 된다. PowerShell, Git,
Python, uv, Node.js와 npm 같은 system runtime은 미리 설치하는 prerequisite이며
repository output이 아니다.

CI는 runtime 설치와 cache 복원까지만 workflow step으로 수행할 수 있다. lint, test,
build, browser smoke를 workflow에 다시 적어 로컬과 다른 경로를 만들지 않고 반드시
canonical entrypoint를 호출한다.

## 필수 도구와 실패 계약

| 도구 | 최소 기준 | 없거나 맞지 않을 때 안내할 복구 방법 |
|---|---|---|
| PowerShell bootstrap | PowerShell 7의 `pwsh` | 호출 shell 또는 CI setup이 PowerShell 7을 설치하고 `pwsh`가 `PATH`에 있는지 확인한다. |
| Git | checkout을 읽을 수 있는 `git` | Git을 설치하고 저장소 root에서 다시 실행한다. |
| Python | `pyproject.toml`의 `requires-python` 충족 | 호환 Python을 설치한 뒤 `uv sync --extra dev --frozen`을 실행한다. |
| uv | lockfile을 읽는 `uv` | uv를 설치한 뒤 `uv sync --extra dev --frozen`을 실행한다. |
| Node.js·npm | `site/package.json`의 `engines.node` 충족 | 호환 Node.js를 설치하고 `npm --prefix site ci --engine-strict`를 실행한다. |
| Ruff·pytest | Python dev environment에 설치 | `uv sync --extra dev --frozen`으로 복구한다. 검사를 건너뛰지 않는다. |
| browser runner·runtime | `site/package-lock.json`에 고정된 runner와 browser | A가 제공할 repository-owned install script를 실행한다. global browser로 대체하지 않는다. |

`pwsh`가 없으면 PowerShell script 자체를 시작할 수 없으므로 호출 shell 또는 CI setup이
bootstrap 실패를 non-zero로 보고하고 위 복구 방법을 출력한다. entrypoint가 시작된 뒤에는
나머지 system prerequisite를 실제 gate 전에 모두 검사한다. 누락, 버전 불일치 또는 lockfile
설치 실패는 non-zero로 종료하며 `SKIP`, warning-only 또는 성공으로 처리하지 않는다.
entrypoint의 실패 메시지는 최소한 다음 정보를 한 줄에 포함한다.

```text
VERIFY <gate> FAIL: <원인>. Recover: <실행할 명령 또는 이 문서의 절>.
```

메시지에는 환경변수 값, credential, request body, raw event 또는 전체 process
environment를 출력하지 않는다.

## Gate 순서

| 순서 | gate | 통과 조건 |
|---:|---|---|
| 1 | prerequisites | 호출 환경이 PowerShell bootstrap을 보장하고 entrypoint가 나머지 필수 도구와 version을 확인하며 silent skip이 0건이다. |
| 2 | Python | frozen dev install, compile, Ruff, 전체 pytest가 성공한다. |
| 3 | canonical web | reducer test와 Python fixture·event contract parity가 성공한다. |
| 4 | hosted site | engine-strict `npm ci`, high audit, typecheck, lint, production build, route test가 성공한다. |
| 5 | drift | `web/`와 `site/`의 공유 contract·fixture가 달라지면 실패한다. |
| 6 | browser | production-like server에서 normal, typed unsupported, explicit fallback 세 case가 terminal까지 완주한다. |
| 7 | hygiene | tracked secret/generated media/raw log가 없고 허용된 요약 artifact만 남는다. |

앞 gate가 실패하면 뒤 gate를 성공으로 보고하지 않는다. 최종 성공 출력은 실행된 gate
목록과 `skipped=0`을 포함하고 exit code 0을 반환한다.

## Real-browser acceptance

세 case는 실제 form에서 repository, ref, mission을 제출해 같은
`POST /api/runs → SSE → terminal` 경로를 사용한다. normal과 fallback은 한 번 실행하고,
unsupported는 동일한 structured input을 새 browser context의 독립된 one-input run 두 번으로
실행한다. 각 run은 한 번만 submit하며 중간 승인, 다음 단계 버튼 또는 test-only shortcut
API를 사용하지 않는다.

### Normal hosted path

- production-like build가 제공하는 form을 제출한다.
- CI secret이나 실제 외부 서비스 없이 deterministic local upstream을 사용한다.
- ordered SSE의 `seq`가 연속이고 exactly one terminal event가 있다.
- Raw API item과 controller/fixture projection이 화면과 event contract에서 구분된다.
- `fixture_fallback` 표식이 없고 normal route의 terminal boundary가 보인다.
- 브라우저 console에 처리되지 않은 오류가 없고 예상하지 않은 network failure가 없다.

### Typed unsupported path

- D1에서 지원하지 않는 mission을 실제 structured form으로 제출한다.
- 두 run 각각 typed `unsupported` terminal이 정확히 한 번 나타난다.
- payment/cake evidence가 0건이며 다른 fixture로 조용히 치환되지 않는다.
- 같은 fixture와 seed의 safe terminal projection은 반복 실행에서 같은 digest를 낸다.

### Explicit fallback path

- test double로 API 또는 stream failure를 의도적으로 만든다.
- fallback은 `fixture_fallback`과 `AUTO / FIXTURE`처럼 사용자에게 보이는 표식을 유지한다.
- submitted-target finding으로 승격하지 않고 exactly one terminal state로 끝난다.
- 의도한 실패 요청 외의 console/network 오류가 없으며 한 번의 submit으로 완주한다.

세 case 모두 #69의 supported/unsupported 판정과 조용한 payment/cake 치환 방지 gate를
재사용한다. browser test가 이 판정을 별도 상수로 복제하면 drift로 간주한다.

## `web/`·`site/` drift 경계

drift gate는 두 tree 전체의 byte identity를 요구하지 않는다. request 의미, SSE parsing,
terminal 처리, Raw Stream 경계, shared fixture와 fallback 표식처럼 사용자가 관찰하는
contract가 달라질 때 실패해야 한다. local과 hosted의 timeout, port, asset path처럼 이름과
이유가 기록된 환경 차이는 허용한다. 허용 목록에 없는 차이는 silent exception으로 두지
않고 focused parity test 또는 문서화된 adapter로 설명한다.

현재 허용된 차이는 local API fallback timeout `1600 ms`와 hosted network timeout
`22000 ms`뿐이다. 이 값은 실행 환경의 time budget이며 request payload, SSE parsing,
terminal, fixture 또는 fallback 의미를 바꾸는 근거가 아니다.

## CI browser artifact

CI는 매 실행마다 `browser-smoke-summary.json` 한 파일만 업로드한다. 파일은 캡처한
browser data를 redaction해서 만들지 않고 assertion counter에서 직접 생성하며 8 KiB를
넘지 않는다. 허용 schema는 다음으로 제한한다.

```json
{
  "schema": "agent24.browser-smoke.v1",
  "browser": "chromium",
  "passed": true,
  "cases": [
    {
      "id": "normal_hosted",
      "passed": true,
      "run_count": 1,
      "submit_count": 1,
      "seq_contiguous": true,
      "terminal_count": 1,
      "raw_stream_visible": true,
      "expected_boundary_visible": true,
      "unexpected_evidence_count": 0
    },
    {
      "id": "typed_unsupported",
      "passed": true,
      "run_count": 2,
      "submit_count": 2,
      "seq_contiguous": true,
      "terminal_count": 2,
      "terminal_digest_stable": true,
      "raw_stream_visible": true,
      "expected_boundary_visible": true,
      "unexpected_evidence_count": 0
    },
    {
      "id": "explicit_fixture_fallback",
      "passed": true,
      "run_count": 1,
      "submit_count": 1,
      "seq_contiguous": true,
      "terminal_count": 1,
      "raw_stream_visible": true,
      "expected_boundary_visible": true,
      "unexpected_evidence_count": 0
    }
  ],
  "failure": null
}
```

`cases`에는 `normal_hosted`, `typed_unsupported`, `explicit_fixture_fallback` 세 fixed ID가
각각 한 번 있어야 한다. counter는 모든 독립 run의 합계이며 `seq_contiguous`와
`expected_boundary_visible`은 각 run이 조건을 충족할 때만 true다. unsupported case에만
`terminal_digest_stable`을 두며 두 terminal의 safe projection digest가 같을 때만 true다.
각 case는 위에 표시된 key 외의 값을 갖지 않는다.

성공하면 `failure`는 null이다. 실패하면 다음 두 key만 가진 object다.

```json
{
  "case_id": "typed_unsupported",
  "code": "unsupported_boundary_failed"
}
```

`case_id`의 closed set은 `prerequisites`, `normal_hosted`, `typed_unsupported`,
`explicit_fixture_fallback`, `artifact_export`다. `code`의 closed set과 허용 조합은 다음과 같다.

| `case_id` | 허용 `code` |
|---|---|
| `prerequisites` | `prerequisite_missing`, `prerequisite_version_invalid`, `dependency_install_failed` |
| `normal_hosted` | `normal_route_failed`, `sequence_gap`, `terminal_count_invalid` |
| `typed_unsupported` | `unsupported_boundary_failed`, `unexpected_payment_evidence`, `terminal_digest_mismatch`, `sequence_gap`, `terminal_count_invalid` |
| `explicit_fixture_fallback` | `fallback_boundary_failed`, `sequence_gap`, `terminal_count_invalid` |
| `artifact_export` | `artifact_policy_failed` |

여러 assertion이 실패하면 gate 순서, browser case 순서, case 내부 assertion 순서에서 처음
실패한 조합 하나만 기록한다. free-form string, 추가 key, 허용되지 않은 조합, 예상 밖 파일
또는 8 KiB 초과는 업로드 전에 실패한다.

artifact upload는 `retention-days: 1`, `include-hidden-files: false`,
`if-no-files-found: error`를 명시하고 pass/fail 뒤 `!cancelled()`에서 실행한다. upload step은
원래 browser test의 실패 status를 성공으로 바꾸지 않는다. 짧은 retention은 secrecy
control이 아니므로 Actions log도 처음부터 fixed code만 출력한다.

다음 항목은 CI artifact와 job log에 올리지 않는다.

- repository/ref/mission 원문, request body, query string과 URL parameter
- run ID, events URL, run context, OpenAI response ID, header, cookie, browser storage,
  token, `.env`, process environment
- raw SSE body, Raw API item, tool argument/result, JSONL과 전체 console/network dump
- arbitrary exception/provider body, HAR, DOM, screenshot, video, trace, rendered HTML,
  generated media와 개인 데이터

실패 분석에 raw evidence가 필요하면 ignored local `artifacts/` 아래에서만 확인하고 CI
artifact에 첨부하지 않는다. UI 변경 PR에는 의도적으로 캡처하고 Raw Stream, credential,
개인 데이터와 local browser chrome을 제거한 screenshot만 별도로 첨부할 수 있으며 Git에
commit하지 않는다. artifact exporter가 허용 목록 밖 파일을 발견하면 업로드 전에 gate를
실패시킨다.

## PR과 release evidence

#70 구현 PR은 다음 내용을 본문에 기록한다.

- 검증한 full commit SHA와 OS, Python, Node, PowerShell version
- canonical entrypoint 한 번의 exit code와 gate별 PASS, `skipped=0`
- normal/unsupported/fallback browser case의 sanitized summary와 artifact 이름
- web/site drift negative test가 실제로 실패한 evidence
- credential, 개인 데이터, generated media, raw run log가 없다는 diff 확인

개별 command를 따로 실행한 로그는 canonical entrypoint 통과를 대신하지 않는다. 위
증거 중 하나라도 없으면 PR을 부분 구현으로 표시하고 #70을 닫지 않는다.
