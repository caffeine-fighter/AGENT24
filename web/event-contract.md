# NIGHTMARE shared run and event contract

> Status: D3.1–D3.8 ratified by Rekhet · 2026-08-01

This document is the single source of truth for the HTTP, JSON, SSE, terminal,
error, reconnect, and compatibility boundary shared by the local FastAPI runtime,
the hosted route, and every first-party frontend. D1 owns product meaning and D2
owns presentation; this contract encodes those decisions without redefining them.

The machine-readable examples live in `fixtures/event-contract.v1.json`. A4 migrates
producers and consumers to those examples. Current implementation differences are
drift, not alternate contracts.

## Contract principles

- One canonical structured request replaces duplicate external-run strings.
- Boundary validation happens before a run exists and always has one typed error shape.
- An accepted run emits an ordered, versioned, provenance-bearing event stream.
- Only published OpenAI SDK tool items are called Raw API items.
- Every accepted run has exactly one semantic terminal outcome.
- New event types and additive fields remain forward-compatible and raw-visible.
- Legacy paths receive an advisory adapter and no new features; removal follows zero first-party use.

## Start a run

### Canonical request

New first-party clients send this exact top-level shape:

```http
POST /api/runs
Content-Type: application/json

{
  "schema_version": "external_target.v1",
  "target": {
    "repository_url": "https://github.com/owner/agent",
    "requested_ref": "main",
    "mission": "5만원 이하의 생일 케이크를 정확히 하나 주문한다"
  }
}
```

| Field | Requirement |
|---|---|
| `schema_version` | Required literal `external_target.v1` |
| `target.repository_url` | Required canonical public `https://github.com/{owner}/{repo}` URL, at most 500 characters |
| `target.requested_ref` | Required branch, tag, or full SHA, at most 200 characters |
| `target.mission` | Required goal and constraints, at most 2,000 characters |

Objects are strict: unknown top-level or target fields are rejected. Credentials,
manifest paths, entrypoints, mode selection, and experiment selection are not user
input. If a supported URL form also encodes a ref, it must resolve to the same
effective value as `requested_ref`; disagreement is `conflicting_input`.

Syntactically unsupported source shapes, including non-GitHub hosts, local paths,
ZIP files, embedded credentials, query strings, and fragments, are rejected before
run creation. A canonical public-GitHub shape that later resolves as inaccessible,
private, missing, or invalid becomes an audited after-202 operational outcome.

### Boundary errors

Invalid JSON and all semantic validation failures return `422 Unprocessable Entity`.
No `run_id` is allocated and no fixture starts.

```json
{
  "error": {
    "code": "invalid_submission",
    "message": "입력 계약을 확인하세요.",
    "fields": [
      {
        "path": "target.requested_ref",
        "code": "required",
        "message": "Ref 또는 commit은 필수입니다."
      }
    ]
  }
}
```

The error object is the only HTTP error shape for this endpoint.

| Field | Requirement |
|---|---|
| `error.code` | Stable machine code: `invalid_submission` or `conflicting_input` |
| `error.message` | Safe Korean fallback sentence; never an exception string |
| `error.fields` | Optional bounded list of `{path, code, message}` entries |

`conflicting_input` covers URL/ref disagreement and a legacy `input` whose trimmed
value differs from `target.mission`. Field paths and codes are canonical English;
the frontend maps them to the ratified visible copy.

### Accepted response

A valid request returns `202 Accepted`:

```json
{
  "schema_version": "run.accepted.v1",
  "run_id": "run_01H...",
  "status": "queued",
  "events_url": "/api/runs/run_01H.../events",
  "mode": "live",
  "deprecations": []
}
```

`schema_version`, `run_id`, `status`, `events_url`, and `deprecations` are required.
`status` is `queued`; later truth belongs to the event stream. `mode` remains an
optional advisory compatibility field during A4 because local and hosted producers
currently use different values. New clients must not derive terminal or evidence
meaning from it.

## Legacy request adapter

The replacement exists now, so legacy support is advisory and bounded.

| Incoming shape | Bridge behavior |
|---|---|
| Canonical `external_target.v1` | Preferred; `deprecations` is empty |
| Unversioned `{"input":"..."}` | Label internally as `legacy_prompt.v0`; do not infer repository, ref, or mission; response includes a deprecation entry |
| Unversioned mixed `input` + `target` | Accept only when trimmed `input` equals trimmed `target.mission`; normalize to `external_target.v1`; otherwise typed `422 conflicting_input` |
| Versioned request with `input` | Reject as `invalid_submission`; canonical clients do not duplicate mission text |

Legacy responses include:

```json
{
  "deprecations": [
    {
      "code": "legacy_prompt.v0",
      "replacement": "external_target.v1"
    }
  ]
}
```

No new feature is added to `legacy_prompt.v0`. A4 removes the adapter only after
the web app, hosted route, smoke scripts, fixtures, and tests have zero external-run
dependence on `input`.

## Subscribe to events

```http
GET /api/runs/{run_id}/events
Accept: text/event-stream
Last-Event-ID: 1
```

Events use the default SSE `message`; there is no `event:` line. The SSE `id` is
the decimal event sequence and the JSON envelope remains inside `data`:

```text
id: 2
data: {"schema_version":"event.envelope.v1","run_id":"run_01H...","seq":2,"timestamp":"2026-08-01T07:03:02.120+00:00","type":"tool_result","origin":"openai_sdk","payload":{"type":"function_call_output","call_id":"call_01H...","output":"{\"scenario_id\":\"life.payment_intent_timeout.v1\"}"},"summary":"call_01H..."}

```

## Event envelope v1

```json
{
  "schema_version": "event.envelope.v1",
  "run_id": "run_01H...",
  "seq": 2,
  "timestamp": "2026-08-01T07:03:02.120+00:00",
  "type": "tool_result",
  "origin": "openai_sdk",
  "payload": {},
  "summary": "optional safe display summary"
}
```

| Field | Requirement |
|---|---|
| `schema_version` | Required literal `event.envelope.v1` |
| `run_id` | Required identity matching the accepted response |
| `seq` | Required zero-based integer, strictly increasing by one per run |
| `timestamp` | Required timezone-bearing ISO 8601 publication time |
| `type` | Required canonical English event type |
| `origin` | Required `openai_sdk`, `controller`, `fixture`, or `system` |
| `payload` | Required JSON value whose meaning is fixed by `type` and `origin` |
| `summary` | Optional safe derived text; never replaces payload |

The exact envelope dictionary is appended once to JSONL, sent through SSE, and
retained by the reducer. Transport layers do not reconstruct it.

### Origin and Raw API identity

| Origin | Meaning | Raw API item? |
|---|---|---|
| `openai_sdk` | Published OpenAI Agents SDK semantic stream item | Yes, only for `tool_call` and `tool_result` |
| `controller` | Typed deterministic planner, Gym, oracle, report, or lifecycle event | No; exact controller evidence is still preserved |
| `fixture` | Named deterministic review or fallback fixture | No; it must retain fixture provenance |
| `system` | Transport, compatibility, or operational condition | No |

An event is an unedited Raw API item only when `origin=openai_sdk` and `type` is
`tool_call` or `tool_result`. `gym.tool_call`, `gym.tool_result`, controller reports,
and fixture items must never be relabeled as OpenAI raw items.

Credentials, personal data, unsafe exception text, and real external-account data
are excluded before publication. The runtime passes only bounded synthetic tools
and sanitized context to the SDK. If a payload cannot be safely published without
editing it, the producer does not publish that payload; it emits a safe
`operation.issue` with `code=system_error`. Once published, SDK payload field names,
values, identity, and ordering are not redacted, summarized in place, or replaced.

## D2 semantic stages

`phase.changed` carries `{ "phase": "..." }` and uses this exact order:

| Order | Phase | Starts when |
|---:|---|---|
| 1 | `PIN` | The accepted target begins immutable source and manifest resolution |
| 2 | `PROFILE` | Source provenance exists and observable behavior evidence is classified |
| 3 | `CRASH` | A supported bounded experiment has been selected and fault execution begins |
| 4 | `AUTOPSY` | Measured baseline/fault evidence is ready for first-divergence analysis |
| 5 | `VACCINE` | A bounded mitigation is proposed from cited evidence |
| 6 | `REPLAY` | Protected same-condition and benign-control verification begins |

The producer emits a phase only when it begins. Unsupported input may end in
`PROFILE`; source failure may end in `PIN`; a run does not emit synthetic future
phases merely to complete the rail.

## Event catalog

### Lifecycle and OpenAI events

| `type` | Expected origin | Meaning |
|---|---|---|
| `run_started` | `system` | Accepted run begins; contains bounded target echo, never resolved provenance |
| `phase.changed` | `controller` | One of the six semantic phases begins |
| `tool_call` | `openai_sdk` | Unedited published SDK function-call item |
| `tool_result` | `openai_sdk` | Unedited published SDK function-call-output item |
| `final_output` | `openai_sdk` | Bounded optional explanation; not controller ground truth |
| `operation.issue` | `system` | Nonterminal typed degradation or failure condition |
| `explanation_offline` | `system` | Deterministic target evidence exists, but the optional SDK explanation is unavailable |
| `run_completed` | `system` | The one canonical terminal event |

`run_failed` and `offline_demo` are legacy producer events. A4 stops producing them;
reducers may recognize old stored fixtures during the migration but must not treat
them as a second v1 terminal.

### Source, planning, Gym, and reporting events

| `type` | Expected origin | Meaning |
|---|---|---|
| `source_descriptor` | `controller` | Immutable `SourceDescriptor`, including full SHA and retrieval provenance |
| `behavior_profile` | `controller` | Evidence-backed `BehaviorProfile` assessments and unknown reasons |
| `pack.selected` | `controller` | Deterministic DomainPack routing evidence; selection is not execution |
| `experiment_plan` | `controller` | Selected fault, target tool, rationale, expected evidence, seed, and budget |
| `gym.baseline.completed` | `controller` | Healthy-twin digest and evidence counts |
| `gym.tool_call` / `gym.tool_result` | `controller` | Exact synthetic trace events, not OpenAI raw items |
| `world.snapshot` | `controller` or `fixture` | Named before/after synthetic state |
| `oracle.report` | `controller` | Deterministic invariant evaluation |
| `damage.updated` | `controller` or `fixture` | Bounded damage projection |
| `failure.detected` | `controller` or `fixture` | Cited measured invariant failure |
| `autopsy.ready` | `controller` or `fixture` | First divergence and cited observations |
| `vaccine.proposed` | `controller` or `fixture` | Proposed bounded mitigation, not yet verification |
| `verification.updated` | `controller` or `fixture` | Partial named gate state |
| `replay.completed` | `controller` or `fixture` | Protected replay result and world state |
| `protected_replay` | `controller` | Same-seed SandboxGym evidence bundle |
| `finding_report` | `controller` | Evidence-backed investigation result |
| `lab_report` | `controller` | Frozen observation/hypothesis/proposal/verification report |

`pack.selected` appears after `behavior_profile` and before `experiment_plan`. A
selection with a stop decision does not imply that a pack or experiment ran.
Unknown event types remain in the stream and do not change projected UI state.

### Canonical payment world projection

R3 requires these fields for the payment fixture:

```json
{
  "logical_orders": 1,
  "charges": 1,
  "fulfillments": 1,
  "total_spend_krw": 49000,
  "wallet_krw": 451000
}
```

Additive domain-specific fields are allowed. Legacy `orders`, mail, calendar, and
file counts do not replace the five payment fields or enter the signature story.

## Operational issues

`operation.issue` uses one safe shape:

```json
{
  "code": "source_unavailable",
  "recoverable": false,
  "fallback_started": true,
  "safe_detail": "public source 또는 ref를 확인하세요."
}
```

Stable after-202 codes are `source_unavailable`, `manifest_missing`,
`manifest_invalid`, `system_error`, and `fallback_demo`. `safe_detail` is optional,
bounded, and sanitized. Provider exception strings, credentials, request headers,
and private source content never appear.

`explanation_offline` is not `fallback_demo`. It means controller-owned submitted-
target evidence already exists and only the optional model explanation is degraded.
The terminal keeps `result_source=submitted`.

## Exactly one terminal outcome

Every accepted run ends with exactly one `run_completed`. Ending the SSE stream
without it is a protocol failure, not an implicit success or fallback.

```json
{
  "submission": {
    "status": "accepted"
  },
  "investigation": {
    "status": "verified_mitigation"
  },
  "operation": {
    "status": "completed",
    "code": null
  },
  "result_source": "submitted"
}
```

| Axis | Stable values |
|---|---|
| `submission.status` | `accepted` (invalid submissions have no run) |
| `investigation.status` | `measured_failure`, `verified_mitigation`, `no_failure_observed`, `unsupported`, `budget_exhausted`, `not_run` |
| `operation.status` | `completed`, `degraded`, `fallback_demo`, `failed` |
| `operation.code` | `null` or one stable after-202 operational code |
| `result_source` | `submitted`, `fixture`, `none` |

Terminal invariants:

- `verified_mitigation` names only passed replay gates; it is not whole-Agent safety.
- `unsupported`, `no_failure_observed`, and `budget_exhausted` do not create a measured finding.
- `result_source=fixture` requires `investigation.status=not_run` and `operation.status=fallback_demo`.
- `explanation_offline` may finish as `operation.status=degraded` while retaining a submitted result.
- Source or manifest failure cannot inherit submitted-target profile or finding labels.
- A legacy `run_failed` observed before `run_completed` is a compatibility condition, never another terminal.

## Fallback behavior

`fallback_demo` is reserved for an incomplete submitted-target analysis followed by
a separate named fixture playback. Fixture events use `origin=fixture`; the UI shows
`source=fixture` and `SUBMITTED TARGET · NOT ANALYZED`. The submitted target keeps
`investigation.status=not_run`.

HTTP `422` never starts a fixture. A transport failure before any live event may
start the local fixture only after the UI visibly changes to the operational
fallback state. A controller result followed by an unavailable OpenAI explanation
uses `explanation_offline`, not a fixture terminal.

## Reconnect, duplicates, and gaps

- `seq` begins at 0 and increases by exactly one.
- The server emits SSE `id: {seq}` and honors `Last-Event-ID` by replaying only events with a greater sequence.
- Without `Last-Event-ID`, the endpoint sends the complete backlog and then live events.
- A client deduplicates byte-equivalent events by `(run_id, seq)`.
- The same `(run_id, seq)` with different envelope content is a protocol error; both observed envelopes remain raw-visible.
- A gap buffers later projection, reconnects from the last contiguous sequence, and resumes only after the missing event arrives.
- An unrecoverable gap or premature stream close becomes a visible protocol failure; the client does not invent a terminal outcome.

SSE and JSONL contain the same envelope objects in the same sequence. Reconnection
does not delete already received evidence.

## Evolution rules

Within `event.envelope.v1`, producers may add optional envelope or payload fields and
new event types. Consumers ignore unknown fields for projection and preserve them in
the Raw Stream. Unknown event types are projection-neutral.

A breaking field removal, rename, type change, or semantic reinterpretation requires
a new major schema discriminator. A client that does not understand the major
envelope version keeps the bytes raw-visible, shows an unsupported-protocol state,
and does not project claims from it. Producers must not emit both old and new
terminal events to simulate compatibility.

## Frontend normalization

The receiving boundary may map wire names to internal reducer names, but retains the
wire envelope unchanged:

- `payload` becomes projection input and an identity-preserved Raw Stream value.
- `type`, `origin`, and `schema_version` remain separately available.
- Unknown types and fields remain in event history.
- Legacy `run_started`, `run_completed`, `run_failed`, and fixture `data`/`raw`
  aliases may be dual-read during A4; new producers write only the v1 contract.

## A4 migration order

1. Add consumer dual-read for v1 envelope/origin/terminal and legacy stored fixtures.
2. Change web and hosted first-party requests to write only `external_target.v1`.
3. Change local and hosted producers to emit v1 envelopes, six phases, operational issues, and one `run_completed` terminal.
4. Add SSE IDs, `Last-Event-ID` replay, duplicate/gap handling, and cross-transport identity checks.
5. Migrate smoke scripts, fixtures, docs, and focused tests; verify zero first-party external-run `input` use.
6. Remove the legacy request adapter and old producer events together; retain archived raw logs outside Git.

## Contract acceptance checks

- Canonical requests contain repository, required ref, mission, and no credential or duplicate input.
- All boundary failures use typed `422` errors and create no run or fixture.
- Accepted responses and event envelopes carry the approved schema versions.
- Every event has one declared origin; only SDK tool events claim Raw API identity.
- The six semantic phases occur in order and only when reached.
- Payment result projections expose logical order, charges, fulfillments, spend, and wallet.
- Every 202 run has one and only one `run_completed` terminal.
- Investigation, operation, and result source remain independent and internally valid.
- Explanation degradation is not fixture fallback.
- SSE, JSONL, and reducer raw values preserve published envelope identity and order.
- Reconnect, duplicate, gap, unknown-event, and unknown-major behavior follows this contract.
- Legacy removal waits for a working v1 replacement and zero first-party consumers.
