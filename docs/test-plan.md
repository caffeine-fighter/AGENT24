# D5a non-leaky test plan

> Status: D5a T1–T4 ratified by Rekhet · 2026-08-01 · implementation and final audit pending

This is the canonical test-design plan for NIGHTMARE LAB's D4 payment slice. It
fixes coverage, held-out construction, execution environments, evidence capture,
and leakage controls before A5 sees final results. D5b still owns repetitions,
quantitative thresholds, stop rules, and final completion.

The machine-readable source of the IDs, quotas, lanes, and artifact rules below is
[`../tests/evals/d5a-plan.json`](../tests/evals/d5a-plan.json).

## Authority and scope

The plan tests the accepted contracts rather than inferring product behavior from
the current implementation.

| Authority | What this plan tests |
|---|---|
| [`nightmare-lab-concept.md`](nightmare-lab-concept.md) | D0 payment truth, measured values, and valid mitigation |
| [`external-agent-contract.md`](external-agent-contract.md) | D1 public-source, evidence, outcome, and safety meanings |
| [`frontend-journey-contract.md`](frontend-journey-contract.md) | D2 R1–R4 journey, copy, fallback separation, and responsive behavior |
| [`../web/event-contract.md`](../web/event-contract.md) | D3 request, event, Raw, terminal, reconnect, and evolution wire |
| [`integration-acceptance.md`](integration-acceptance.md) | D4 accepted production slice and independent-proof rule |

Research, Stock, Ticket, Adhoc, participant static profiles, hosted prebuilt
sequences, and browser-only fixtures remain visible regression inventory. They
cannot substitute for a missing payment-P0 acceptance case.

## Activation and dependency graph

D5a is ratified, but A5 visible-case measurement does not begin until A4 has completed
the D3 migration and conformance surfaces. The final campaign waits for D5b. This
order prevents implementation feedback from consuming the final held-out set.

```text
D5a plan committed
        ↓
A4 caller/producer/transport/reducer migration
        ↓
Visible regression + conformance tests
        ↓
A5 visible-case initial measurements
        ↓
D5b repetitions, thresholds, completion
        ↓
Final implementation/runner freeze
        ↓
Materialize one-use held-outs
        ↓
Publish case/reference/scorer commitments
        ↓
A6 final campaign
```

## T1 · Coverage contract

Every D0–D4 contract row must map to at least one named case before the campaign
runs. A missing mapping fails traceability; it is not converted into a residual-risk
note after results are known.

| Family | Exact intent | Planned implementation surface |
|---|---|---|
| `C1_start_boundary` | Canonical request/acceptance, typed 422, no-run rejection, bounded legacy deprecation | `tests/acceptance/test_start_boundary.py` |
| `C2_source_provenance` | Independent public pin, manifest identity, private/invalid handling, no submitted-code execution | `tests/acceptance/test_source_provenance.py` |
| `C3_controller_success` | Six phases, D0 values, divergence, protected gates, one successful terminal | `tests/acceptance/test_controller_success.py` |
| `C4_honest_outcomes` | Unsupported, no-failure, budget, separate fallback, and explanation degradation | `tests/acceptance/test_honest_outcomes.py` and reducer checks |
| `C5_event_transport` | Version/origin/order, Raw value identity, reconnect, duplicate/gap, unknown type/major | `tests/acceptance/test_event_transport.py` and `web/tests/event-protocol.test.mjs` |
| `C6_browser_journey` | R1–R4 at `320x800` and `1440x1000`, keyboard/focus, overflow, console/network | `site/tests/acceptance/journey.spec.ts` with Playwright Chromium |
| `C7_safety_privacy` | No credentials/PII/unsafe exception, real effects, code execution, path/host expansion, or forbidden Git artifacts | `tests/acceptance/test_safety_boundaries.py` and browser network assertions |

The exact visible case IDs and family-level assertions are frozen in the JSON
manifest. Tests may be split mechanically when a file grows, but IDs and contract
meaning do not change without a D5a amendment.

### Existing visible regression baseline

These commands run before held-out materialization. Their results are calibration
and regression evidence, never final held-out evidence.

| Layer | Command |
|---|---|
| Python syntax | `python -m compileall -q src tests scripts` |
| Python lint | `ruff check src tests scripts` |
| Python suite | `python -m pytest -q` |
| Web reducer | `node web/tests/core.test.mjs` |
| D2 contract | `node web/tests/d2-review.test.mjs` |
| D3 contract | `node web/tests/event-contract.test.mjs` |
| D4 contract | `node web/tests/integration-acceptance-contract.test.mjs` |
| Sites install | `cd site && npm ci --engine-strict` |
| Sites static checks | `cd site && npm run typecheck && npm run build && node --test tests/rendered-html.test.mjs` |

### Planned conformance commands

The files do not exist until A4/A5 preparation implements them. A command is
activated only when all named files exist; missing files are reported as
unimplemented coverage rather than silently skipped.

```text
python -m pytest -q \
  tests/acceptance/test_start_boundary.py \
  tests/acceptance/test_source_provenance.py \
  tests/acceptance/test_controller_success.py \
  tests/acceptance/test_honest_outcomes.py \
  tests/acceptance/test_event_transport.py \
  tests/acceptance/test_safety_boundaries.py

node web/tests/event-protocol.test.mjs

cd site && npx playwright test tests/acceptance/journey.spec.ts
```

## T2 · Held-out policy

Held-out families and scorers are public; concrete values, mutation positions, and
case ordering are one-use. This tests implementation behavior without turning the
validation recipe into an unverifiable private judgment.

| Held-out group | Quota | Materialization rule |
|---|---:|---|
| Public owner-manifest target | 1 | After D5b ratification and the final implementation/runner freeze, independently resolve a public repository to a full SHA, fetch exact manifest bytes, then submit the full SHA to the product |
| Boundary/source/manifest shapes | 6 | Select concrete missing, conflicting, visibility, ref, and manifest variants after freeze |
| Terminal/degradation shapes | 5 | One each for unsupported, no-failure, budget exhaustion, separate fallback, and explanation degradation |
| Event protocol mutations | 5 | Select reconnect cut, duplicate, conflicting duplicate, gap, and unknown-event positions after freeze |
| Browser surface/viewport pairs | 8 | Materialize one unseen valid data, ordering, or degradation variant for R1–R4 at both `320x800` and `1440x1000` |

The fixed seed-42 payment fixture remains visible because D4's claim is deliberately
bounded to that condition. Independence comes from state, ledger, trace, oracle,
source, and transport references—not from pretending the canonical fixture is secret.

### One-use lifecycle

1. Complete A5 initial measurement on visible conformance cases and ratify D5b.
2. Freeze the exact implementation and runner commits for the final campaign.
3. Generate the concrete case set in a clean CI workspace.
4. Record `case_set`, expected-reference, and scorer SHA-256 commitments before the
   system under test starts.
5. Execute the set once and mark it consumed regardless of outcome.
6. If a case fails, publish it as an ordinary regression, repair against the visible
   suite, freeze a new commit, and materialize a fresh held-out set.

Any behavior-changing implementation or scorer edit after step 1 invalidates the
measurement basis and returns to visible-case measurement before another D5b freeze.

Cleartext cases are never committed before execution. The commitment and subsequent
burn record remain sufficient to prove which frozen set was used.

## T3 · Environment and evidence

All lanes record the resolved OS image version, Python patch, dependency-lock hashes,
application commit, runner commit, and plan version. No lane performs real external
side effects or accepts user credentials.

| Lane | Network and provider rule | Purpose |
|---|---|---|
| `E1_deterministic_ci` | Network denied; OpenAI absent or mocked | Unit, controller, API, reducer, and visible regression gates |
| `E2_github_source_pin` | Read-only `api.github.com` access for the one submitted public target; no OpenAI key | Record independent full SHA/manifest bytes, then exercise the product with that immutable SHA |
| `E3_sdk_raw_identity` | Network denied; official SDK adapter with deterministic mocked Responses client | Compare producer, JSONL, SSE, and reducer Raw values |
| `E4_browser` | Localhost only; Playwright Chromium pinned in `site/package-lock.json` | Real-browser R1–R4, accessibility, console, network, and overflow evidence |

Every lane uses Ubuntu 24.04, Python 3.12.x, Node 22.13.0, and `TZ=UTC`. Playwright is
the only new test harness permitted by D5a; it is not a second application framework.

### Evidence bundle

CI retains sanitized evidence for seven days under a build-specific artifact name.
Git receives only the planned `docs/acceptance-results.md` after the audit; that
summary contains identities, outcomes, digests, and residual risks but not raw
run logs or generated media.

| CI-only artifact | Required content |
|---|---|
| Test results | Sanitized JUnit and JSON case results with stable IDs |
| Reference comparison | Expected/actual normalized digests and cited controller paths |
| Browser evidence | DOM/accessibility, console/network capture, and screenshots |
| Held-out record | Commitment, materialization time, consumed/burn status, and artifact digests |
| Environment manifest | OS image, runtimes, lock hashes, commits, timezone, and network lane |

Credentials, personal data, unsafe exception text, cleartext held-outs before use,
raw run logs, and generated media never enter Git.

## T4 · Leakage controls

The validation components are: the frozen product implementation, deterministic
scorers, public family manifest, one-use dataset, independent source/controller/Raw
references, and GitHub CI runner.

| Mandela pattern that fires | Independence fix |
|---|---|
| Tautology | Controller state/ledger/trace and producer payloads grade UI/report projections; fixture-authored display values never grade themselves |
| Verifier = designer | Scorers and family definitions are committed before results, concrete cases are generated after freeze, and CI executes the frozen recipe |
| Wrong null hypothesis | Fallback uses a distinct target fingerprint and must contain zero submitted-provenance carryover, so relabeling the cake fixture cannot pass |

Existing `tests/evals/cases.yaml`, review fixtures, and any failed held-out case are
visible regression data. They cannot be counted again as a final holdout.

## Implementation tasks

The slices follow existing ownership and remain independently verifiable. A/B/C may
land them in parallel after the shared D3 models are available; D owns traceability,
commitments, and the independent audit rather than replacing their implementation.

| Task | Owner | Acceptance criteria | Dependency |
|---|---|---|---|
| Q1 · Freeze manifest and traceability validator | D | JSON plan parses; every family/case/contract/owner/implementation field is complete; artifact rules validate; D5b fields remain absent | D5a ratified |
| Q2 · API/source/transport/safety conformance | C | C1, C2, backend C5, and backend C7 cases pass against versioned models, SSE rules, and no-code-execution boundary | A4 backend wire migration |
| Q3 · Controller/outcome evidence | B | C3 and controller C4 cases cite independent state/ledger/trace/oracle evidence | A4 semantic-event migration |
| Q4 · Reducer/protocol/browser conformance | A | C4/C5 reducer mutations and C6 real-browser cases pass on the production path | A4 frontend/transport migration |
| Q5 · CI lanes and artifact retention | A + D | Four lanes enforce network/provider policy and retain only sanitized seven-day artifacts | Q1–Q4 |
| Q6 · A5 initial measurement | D | Visible conformance cases produce timing, variance, flake, and failure evidence without materializing final held-outs | Q1–Q5 |
| Q7 · Final freeze, materialization, and A6 campaign | D | After D5b, commitments precede execution; the one-use set is consumed once and summarized without forbidden artifacts | Q6 and D5b |

## D5b boundary

A5's initial measurements activate D5b after the materializer and scorer have been
dry-run on visible dummy cases, without generating or exposing the final completion
campaign. D5a deliberately does not decide:

- repetition count or consecutiveness;
- timing limit or variance tolerance;
- acceptable pass rate;
- early-stop or retry rule;
- final closure rule for product acceptance or presentation claims.

Those choices require measured timing, flake, and failure-distribution evidence and
return to Rekhet as D5b rather than being inferred by A6.
