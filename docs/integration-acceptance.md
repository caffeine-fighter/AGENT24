# D4 integration acceptance contract

> Status: D4 I1–I4 ratified by Rekhet · 2026-08-01 · implementation audit pending

This document defines what “accepted end to end” means for NIGHTMARE LAB's first
integrated product slice. It does not claim that the current implementation passes,
and it does not choose D5's exact cases, held-outs, environments, repetitions, or
thresholds.

## Authority and dependencies

D4 composes, but does not redefine, the four earlier contracts:

| Gate | Canonical artifact | D4 dependency |
|---|---|---|
| D0 | [`nightmare-lab-concept.md`](nightmare-lab-concept.md) | Payment fixture truth, measured values, and valid-mitigation gates |
| D1 | [`external-agent-contract.md`](external-agent-contract.md) | Public-source support, provenance, experiment meaning, and evidence-limited claims |
| D2 | [`frontend-journey-contract.md`](frontend-journey-contract.md) | R1–R4 product journey and honest visual states |
| D3 | [`../web/event-contract.md`](../web/event-contract.md) | Versioned request/event wire, Raw boundary, one terminal, reconnect, and migration |

If implementation behavior conflicts with these artifacts, the implementation is
drift. A newly discovered product alternative returns to its owning D gate instead
of being silently accepted by A5.

## I1 · Accepted slice

The canonical integrated slice is the same-origin production web application calling
the local FastAPI run API, which invokes the controller and publishes the resulting
stream through SSE to the production reducer.

| Surface | Accepted meaning |
|---|---|
| Entry | One `external_target.v1` request containing the D1 public GitHub repository, one effective ref, and the D0 cake mission; no duplicate legacy `input` |
| Source | The ref resolves once to a full immutable SHA and the allowlisted owner manifest is retained with its path, hash, resolver, and adapter identity |
| Execution | The controller selects only the D0 payment `commit_then_timeout` synthetic package with seed `42`; submitted repository code is not executed |
| Product | The same run reaches the production reducer and renders the D2 R1–R4 meanings while preserving the D3 event envelope |

Hosted and static first-party surfaces must conform to D3 before migration is
complete, but a prebuilt hosted sequence or browser fixture cannot prove this
integrated slice. The bounded participant static-profile path is P1 compatibility
evidence and cannot replace the owner-manifest payment run for D4.

## I2 · Successful completion

A successful product run requires the complete evidence chain below. Merely rendering
a result card, finishing an SSE stream, or replaying a fixture does not count.

| Required phase | Acceptance evidence |
|---|---|
| `PIN` | Independently checkable full SHA and manifest provenance |
| `PROFILE` | Evidence-backed observable profile and deterministic package selection |
| `CRASH` | Controller-owned baseline and payment timeout fault evidence |
| `AUTOPSY` | Measured invariant violation and the cited first divergence |
| `VACCINE` | Bounded mitigation explicitly labeled as proposed, not yet verified |
| `REPLAY` | Same-seed protected replay, retained task success, benign control, and blanket-block rejection |

The run then emits exactly one `run_completed` terminal with:

```text
submission.status = accepted
investigation.status = verified_mitigation
operation.status = completed
result_source = submitted
```

The UI must show the D0 before/after evidence on the same scale, preserve the five D1
evidence-strength labels, and bound the claim to the named synthetic condition.

## I3 · Honest boundaries

D4 acceptance includes truthful non-success behavior. These are distinct product
meanings, not alternative labels for the same fallback state.

| Boundary | Required product behavior |
|---|---|
| Rejected before run | Invalid or conflicting input returns the typed D3 `422` error and creates no run |
| Accepted non-finding | A supported submission may finish as `unsupported` or `no_failure_observed`; it retains submitted provenance but never becomes a measured failure or safety certificate |
| Operational interruption | Source or transport failure emits a nonterminal typed operational issue and still leads to exactly one terminal outcome |
| Fixture fallback | Any separate deterministic fixture is labeled `result_source=fixture`, carries no submitted-target finding, and cannot inherit submitted provenance as analysis evidence |

The legacy string adapter is compatibility evidence only. It cannot be used as the
normal D4 entry path or as proof that first-party migration is complete.

## I4 · Independent proof

Evidence used to accept the system must not be generated solely by the component or
display surface whose correctness it is meant to prove.

| Claim | Minimum independent reference |
|---|---|
| Resolved source | Expected full SHA recorded before resolver execution; manifest path/hash checked against the exact retained bytes |
| Damage and mission result | Controller-owned world state, append-only ledger, trace, and deterministic invariants rather than agent prose or UI labels |
| Mitigation effectiveness | Same-seed protected replay plus task-success, benign-control, and blanket-block gates |
| Raw API Stream | Published OpenAI tool payload captured at the producer boundary and value-identical after canonical JSON normalization in JSONL, SSE, and reducer raw state |
| UI result | Reducer projection compared with the controller evidence and terminal axes, not with another frontend fixture |

Fixture-derived expected values, self-reported model conclusions, and UI-to-UI
comparisons are circular evidence and cannot pass D4.

## A5 audit gate

A5 is autonomous evidence collection after all three conditions exist:

1. A4 has migrated the normal caller, producer, transport, and reducer to D3.
2. D5a has ratified the non-leaky case, held-out, environment, and evidence design.
3. Independent source and outcome references have been recorded before execution.

A5 passes only when one production-path audit satisfies I1–I4 without a contract
exception. A mechanical failure returns to the owning implementation lane; a result
that would require weaker evidence, broader P0 support, or a different product claim
returns to Rekhet as a D0–D4 amendment.

## Current implementation delta

This snapshot is coordination evidence, not part of the accepted meaning. At
`origin/main` commit `808e9aa`, the candidate path still has the following D3 drift:

- local and hosted start requests do not use the canonical request version;
- event envelopes do not consistently carry D3 version and origin;
- the production journey still begins with `CLONE` rather than `PIN` and `PROFILE`;
- `run_failed` and `offline_demo` remain active producer/reducer semantics;
- SSE producers do not yet provide sequence IDs and `Last-Event-ID` resume.

Until those items are migrated and audited, D4 is ratified as the acceptance target
but not satisfied by the implementation.

## Deliberately deferred

D4 does not select the full test matrix, exact held-outs, execution environment,
repetition counts, quantitative thresholds, presentation artifacts, or rehearsal
rules. D5a, D5b, and D6 own those decisions at their recorded activation gates.
