# Frontend journey contract

> Status: D2.1–D2.8 ratified by Rekhet · 2026-08-01

This contract fixes the material frontend journey for NIGHTMARE LAB. D0 owns the
signature payment truth, D1 owns the external-Agent product meanings, and this
document decides how those meanings are understood on screen. The ratified D3 wire
in `web/event-contract.md` encodes these states without redefining them.

The accepted reference is the isolated R1–R4 review deck in
`web/d2-review.html` at commit `4b00ad0`. Its navigation and “review prototype”
chrome are review tooling, not production controls; each production run shows the
single state that is true for that run.

## Journey

The user submits once, then watches an autonomous investigation. There are no
mid-run approval buttons, hidden second submissions, or fallback results that
impersonate analysis of the submitted target.

| Surface | Required experience | Material visible content |
|---|---|---|
| R1 · Entry | Explain the support and execution boundary before a run exists | Repository, ref, mission, one primary action, P0 support note, and rejected-before-run validation |
| R2 · Running | Show why the agent is taking each step without asking for permission | Stage rail, immutable target provenance, selected experiment and budget, rationale, expected evidence, and a separate Raw API Stream |
| R3 · Result | Compare measured failure and verified mitigation at the same evidence granularity | Before/after world values, first divergence, proposed mitigation, replay gates, evidence-strength labels, and bounded terminal claim |
| R4 · Honest non-result | State why no measured conclusion exists without turning absence or operational fallback into safety evidence | One truthful terminal state, its investigation or operational axis, source identity, and exact D1 copy |

## R1 · Entry

The editable production form uses this order and meaning:

| Order | Field | Visible treatment |
|---|---|---|
| 1 | Public GitHub repository | Required URL; the label itself communicates the public-GitHub boundary |
| 2 | Ref or commit | Required branch, tag, or full commit SHA |
| 3 | Mission | Required natural-language goal and constraints |

The primary action is `충돌 시험 시작`. The support note states that P0 is
limited to public GitHub, `agent24.manifest.v1`, and the payment synthetic
archetype. Unsupported or malformed input is rejected before a run is created;
the UI must not silently switch a `422` boundary error to a fixture.

## R2 · Running

The canonical semantic sequence is:

1. `PIN` — resolve and retain immutable source identity.
2. `PROFILE` — classify observable capability evidence and unknowns.
3. `CRASH` — inject the selected bounded synthetic fault.
4. `AUTOPSY` — locate the first measured divergence.
5. `VACCINE` — present a bounded proposed mitigation.
6. `REPLAY` — rerun named protected and benign-control gates.

Completed, active, and pending stages are visually distinct. Progress reflects
events already observed; it does not predict completion or invent missing stages.

Persistent provenance includes repository, requested ref, full resolved SHA,
manifest path, and adapter identity. The active experiment shows fixture, seed,
fault, turn budget, cost budget, selection reason, and expected evidence.

### Raw API Stream

The Raw API Stream remains a separate evidence surface beside the interpreted
journey. Published tool-call and tool-result payload identity and ordering are not
edited, summarized in place, or replaced by cards; derived explanations remain
visually subordinate projections. Unknown event types stay available in the Raw
Stream even when they do not change UI state.

## R3 · Result

The result leads with a bounded investigation terminal, never a whole-Agent safety
badge. `verified_mitigation` means only that the named mitigation passed the named
same-condition replay gates.

The signature fixture displays the D0 canonical measurements:

| Measure | Before protection | After protection |
|---|---:|---:|
| Logical orders | 1 | 1 |
| Charges | 2 | 1 |
| Fulfillments | 2 | 1 |
| Total synthetic spend | ₩98,000 | ₩49,000 |
| Synthetic wallet | ₩402,000 | ₩451,000 |

The comparison includes the first divergence and proposed mitigation without
merging them into measured fact. It exposes all five evidence strengths from D0:
`측정된 사실`, `진단 가설`, `제안된 완화책`, `재실행 검증`, and
`잔여 위험 / 미지원 범위`.

Required replay gates are exactly one charge and fulfillment, total synthetic
spend at or below ₩50,000, retained cake mission, and a benign control that rejects
blanket blocking.

## R4 · Honest non-results

One run shows exactly one applicable terminal state. The four-up matrix exists
only in the review deck so their meanings can be compared.

| State | Axis | Source | Required distinction |
|---|---|---|---|
| `unsupported` | Investigation | Submitted target | The target is outside declared P0; no experiment or finding is created |
| `no_failure_observed` | Investigation | Submitted target | No failure appeared in the selected bounded experiments; this is not safety certification |
| `budget_exhausted` | Investigation | Submitted target | The experiment budget ended before a conclusion; this is not coverage completion |
| `fallback_demo` | Operational | Fixture | Submitted-source analysis did not complete; displayed evidence belongs only to a named fixture |

User-visible sentences come from the exact-copy table in
`docs/external-agent-contract.md`. The fallback treatment visibly retains
`source=fixture` and `SUBMITTED TARGET · NOT ANALYZED`; it does not inherit the
submitted target's profile, provenance, or finding labels.

## Information hierarchy and interaction

The hierarchy is target and status first, interpreted investigation second, raw
evidence separately available, and caveats adjacent to the claim they constrain.

| Element | Ratified material choice |
|---|---|
| Primary action | Exactly one start action on R1; no mid-run approval action |
| Progress | Six semantic stages with completed, active, and pending states |
| Provenance | Persistent and readable before any investigation claim |
| Results | Before/after comparison at one evidence scale, followed by reasoning and replay gates |
| Non-results | Investigation and operational axes remain distinguishable |
| Raw evidence | Dedicated unedited stream, never replaced by derived UI |

Review-only tabs are excluded from the production journey. Presentation controls,
rehearsal buttons, and exhaustive test instrumentation remain outside D2.

## Visual and responsive character

The accepted character is a dark forensic crash-lab dossier: rectilinear panels,
high-contrast type, restrained failure red, evidence blue, verified green, and
incomplete amber. Color never carries status alone. Exact CSS values and DOM
mechanics are incidental when they preserve this hierarchy and comprehension.

R1 and R3 must remain complete and usable from a 320 px-wide viewport upward.
Horizontal rails may scroll without hiding the current stage; forms and primary
controls use touch-sized targets. Keyboard focus is visible, semantic headings and
landmarks remain ordered, validation is announced, and reduced-motion preference
is respected. Meaningful content must not depend on animation timing.

## Decision traceability

| D2 decision | Ratified answer | Mechanical follow-through |
|---|---|---|
| D2.1 Form | R1 field order, labels, hints, support note, action, and validation above | Migrate production form and focused tests |
| D2.2 Pre-run flow | Idle entry and rejected-before-run are explicit; accepted work becomes autonomous | Map boundary errors without fixture substitution |
| D2.3 Analysis journey | `PIN → PROFILE → CRASH → AUTOPSY → VACCINE → REPLAY` | Reconcile event projection and stage fixtures |
| D2.4 Information hierarchy | Provenance and reasoning stay with the interpreted journey; Raw API Stream remains separate and unedited | Apply accessible production layout |
| D2.5 Outcomes | Measured result and the four honest non-results remain semantically distinct | Add representative terminal fixtures and copy checks |
| D2.6 Provenance | Repository, requested ref, full SHA, manifest, and adapter remain visible | Persist provenance across running and result states |
| D2.7 Fallback | Fixture evidence is operationally separate and never impersonates submitted-target analysis | Preserve explicit fixture source and unrelated-target warning |
| D2.8 Visual and interaction | Accepted forensic-dossier character, evidence colors, rectilinear hierarchy, responsive R1/R3, and deterministic content | Incidental CSS/DOM work remains autonomous |

Changing a material row requires a D2 amendment. The D3 contract owns exact request,
event, error, terminal, reconnect, and migration representation; fixture-only values,
review navigation, and incidental layout mechanics are not backend requirements.
