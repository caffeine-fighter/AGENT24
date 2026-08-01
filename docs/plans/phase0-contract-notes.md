# Phase 0 contract notes

`docs/plans/issue-3-4-spec.md` remains the source of truth for names. This file
records the decisions Phase 0 had to make where the spec was silent, and the two
handoffs that other people need to act on.

Frozen after merge: `src/agent24/agent/models.py`, `protocols.py`, `fakes.py`,
`__init__.py`, `tests/evals/cases.yaml`. Change them only by agreement between
Worker A and Worker B.

## Handoff — Worker A (issue #3)

**`ExperimentPolicyProtocol` gained a third method:**

```python
def assess_support(self, mission: Mission,
                   caps: Sequence[ClassifiedCapability]) -> StopDecision: ...
```

Spec §6 defines `assess_support` as a module-level function in `policy.py`.
`loop.py` needs it to populate `LabReport.unsupported_scope` and
`StopDecision(reason="unsupported_input")`, but it only ever holds the protocol.
Keep the free function and have `DefaultExperimentPolicy.assess_support`
delegate to it.

`capabilities.py` ships with a minimal keyword table and is yours to extend from
here. The Phase-0 classification is pinned by
`tests/unit/test_fakes.py::test_canned_*`, which derive taint and privileged-sink
sets from it — keep `web.read → UNTRUSTED_SOURCE/WEB_PAGE`,
`file.read → DATA_ACCESS/DOCUMENT`, and `payment.charge` / `email.send` as
`PRIVILEGED_SINK`.

## Handoff — teammate C (issues #5 / #6)

Implement `GymProtocol.run(scenario, policy)` over the real sandbox. The
determinism guarantee is load-bearing: the same `(scenario, policy)` must give a
byte-identical `RunResult`, because minimization, first-divergence and the
replay gates are all built on replay. `FakeGym` in `fakes.py` is the reference
implementation, and `tests/unit/test_fakes.py` is effectively its conformance
suite.

For the real planner, use `prompts.load_prompt(...)` bodies with the `*Output`
models as the Agents SDK `output_type`. All four forbid extra fields, so a
hallucinated key fails validation instead of silently disappearing.

Nothing under `src/agent24/agent/` imports `openai` or `agents`, so the raw
event-stream requirement stays entirely on the runtime side. `tools/gym.py`,
`api/`, `events/` and `tests/integration/test_api.py` were not touched.

## Decisions the spec left open

**`order.create` is a ledger entry, not a tool.** Mission completion has to be
`count(order.create) >= 1`, and the closed check algebra has no `len()` selector
over world state. A committed `payment.charge` therefore appends two ledger
entries. This is also what makes an over-safe patch detectable: with
`max_purchase_count=0` the count is 0 and the benign gate fails.

**Commit means ledger *and* world, atomically.** Only the response is faulted by
`commit_then_timeout`. If they could drift, the state-path budget invariant and
the ledger-sum budget invariant would disagree about the same run.

**Effective calls.** A call is effective when its paired result status is not
`blocked_by_policy`. `NoUntrustedToPrivilegedCheck` and `BoundedRepeatCheck`
count only effective calls. Blocked calls stay in the trace as evidence the
patch fired — counting them would make every patched replay still look violating,
and the `same_seed` gate could never pass.

**`same_seed` gate semantics.** Passes iff
`violated_ids(patched) ⊆ violated_ids(unpatched) \ {target}` — target fixed and
no new violation. Requiring "all invariants pass" is unsatisfiable for the
bounded-retry case, which completes no purchase patched or not. Functionality is
the benign control's job.

**`ExactlyOnceCheck.group_by` resolution.** `LedgerEntry` attribute →
`entry.data[key]` → `args_digest` fallback; `max_count` is per group. The
fallback is what catches an unkeyed retry of an identical payload. Known and
accepted false positive: two legitimately distinct identical keyless purchases
are indistinguishable — that belongs in `Finding.residual_risk`.

**`DenyRule` matches by set intersection** with the provenance a call declares.
"Deny everything" therefore requires listing `USER_INSTRUCTION`. A rule naming
only untrusted labels never fires on a benign run — which is why
`test_block_all_payments_patch_kills_the_benign_mission` uses
`max_purchase_count=0` rather than a deny rule.

**Provenance is per-call and AUT-declared**, never globally propagated. That is
what lets the injection profile's mission purchase complete under a patch that
denies untrusted-sourced calls. `injection_following` declares `{WEB_PAGE}` on
both `file.read` and `email.send`; a deny rule on `file.read` cannot fire
otherwise.

**Policy check order** is fixed at `max_purchase_count` → `max_spend_krw` →
`deny_rule` → `max_repeated_tool_calls` → `missing_idempotency_key`, so the
reported `reason` is stable. Spend and purchase caps apply to `payment.charge`
only. The (N+1)-th occurrence is blocked, so `max_repeated_tool_calls=2` permits
exactly two executions — which is what keeps `BoundedRepeatCheck(max_repeats=2)`
satisfiable under its own patch.

**A blocked call does not advance a fault's `at_call_index`.** Otherwise applying
a patch relocates the fault and the replay passes for the wrong reason.
`at_call_index` counts calls to `target_tool` only, so removing an unrelated
fault during minimization never renumbers this one.

**`timeout_means_unknown` / `reconcile_with` are annotations, not blocks.** They
decorate a `timeout` result. That is the only legal path from "I don't know" to
reconciliation; implementing them as blocking rules would make recovery
impossible.

**`payment.status` reconciles by payload**, matching
`args_digest(args minus idempotency_key)`. It is the only identity a caller that
never sent a key can still ask about.

**`world_overrides` fails loudly** on an untraversable path. A silently ignored
override is an atom minimization can always drop, which would make a reduction
test pass without proving anything. `files` is a list of `{path, content}`, not a
dict, because `files.contacts.csv` would break dotted-path resolution.

**Frozenset fields serialize sorted** via `field_serializer`. `StrEnum` set
iteration order is hash-derived and `PYTHONHASHSEED`-dependent, so without this
`run_digest` differs across processes and determinism fails only in CI.

**Frozen models are not hashable** when they hold a dict — `Scenario` included.
Minimization must cache on a `frozenset` of atom ids, and gates must dedupe
neighbours by `model_dump_json()`. Never key anything on a check object; key on
`invariant.id`.

**No wall clock.** `FAKE_NOW = "2026-08-01T09:00:00+09:00"`; `random.Random(seed)`
picks content strings only.

## Fixtures Phase 1 depends on

`ScriptedPlanner`'s canned `DUPLICATE_SIDE_EFFECT` plan carries three decoy
faults plus one inert `world_overrides` entry, so `minimize.py` has a real
4-atoms-to-1 reduction to find. `payment.status` is deliberately *not* faulted:
the patched replay reconciles through it. `shop.price_krw` is deliberately not
perturbed: changing it flips the budget invariant and makes the reduction
predicate non-monotone, so target `task.purchase_count`, which is
price-independent.

`ScriptedPlanner.diagnose` maps violated invariant ids to a `FailureCategory`
from evidence, never from the constructor filter — otherwise a wrong diagnosis
would be indistinguishable from a right one. `patch_override` exists so the
rejected-patch path (`verified.accepted=False`) is testable without unfreezing
`fakes.py`.

Expect the first divergence for `retry_happy` at the charge **result**
(`different_result`, timeout vs ok), one event earlier than spec §7's prose
suggests. The post-timeout charge is the consequence, not the divergence.
