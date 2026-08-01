# K-Skill source-intake and risk-profile contract

This document covers the source-intake/provenance slice of issue #61 and the
first executable mapping slice of issue #115. The latter is deliberately a
separate contract: metadata intake still never authorizes upstream execution.

## Boundary

The checked-in catalog reviews 13 P0 K-Skill declarations at the exact public
commit `06d017ac05317da31ab2c8d6a9accf4ad4db70ad` and root tree
`e7d5ac2fb2984cb8c81c28d07ce4eceaa020bbb7`.

| Surface | Skills |
|---|---|
| Ticket and reservation actions | `catchtable-sniper`, `express-bus-booking`, `intercity-bus-booking`, `ktx-booking`, `srt-booking` |
| Account, local, legal, or document actions | `court-payment-order-assistant`, `iros-registry-automation`, `k-skill-cleaner`, `kakaotalk-mac`, `popbill` |
| Read-only research and market lookup | `keris-academic-search`, `korean-stock-search` |
| Public lookup with a submitted report | `lovebug-report` |

Each profile records only a human-reviewed declaration summary, opaque upstream
profile tags, normalized declared tool names and capabilities, trust boundaries,
protected assets, side-effect class, approval/credential/legal/handoff
requirements, non-executable domain and fault-family hints, and three provenance
records:

- `<skill>/skill.json` path, Git blob SHA, and size
- `<skill>/instruction.md` path, Git blob SHA, and size
- `.claude-plugin/plugin.json` path, Git blob SHA, size, and 123-member count

Generated `SKILL.md`, packages, scripts, environment values, accounts, browsers,
and skill bodies are not loaded by the adapter. The bounded upstream plugin
manifest is read only by the offline snapshot verifier to prove catalog
membership. Upstream profile tags are not translated into owner-authored
`AgentManifest` or observed `BehaviorProfile` facts. The catalog is a standalone
triage registry.

Every result keeps `execution_authorized=false`, `max_experiments=0`,
`observation_status=not_executed`, and an empty `failure_claims` collection. A
profile therefore means “declared surface reviewed,” not “target tested,”
“failure observed,” or “safe.” A separate synthetic mapping must make any
executable fixture decision and preserve this claim boundary.

## Reviewed synthetic mapping

`KSkillMappingRegistry` binds the exact source commit, tree, catalog digest,
skill id, and each declared hypothesis to either an allowlisted archetype or a
specific typed unsupported reason. It does not translate upstream tool names
into `AgentManifest`, use fuzzy domain routing, or infer a fixture from prose.
The current snapshot accounts for all 43 declared hypotheses: 15 Ticket
hypothesis mappings are executable and 28 remain explicitly unsupported.

Each Ticket profile has one distinct default archetype so five real fixtures
are exercised rather than one replay being counted five times.

| Skill | Default synthetic fixture | Declared hypothesis |
|---|---|---|
| `catchtable-sniper` | `ticket.stale-availability.v1` | hold/cancel reconciliation |
| `express-bus-booking` | `ticket.commit-then-timeout.v1` | partial success followed by retry |
| `intercity-bus-booking` | `ticket.cancel-ambiguity.v1` | hold/cancel reconciliation |
| `ktx-booking` | `ticket.event-identity-confusion.v1` | scoped approval: target |
| `srt-booking` | `ticket.price-fee-currency-drift.v1` | scoped approval: amount/currency |

Every executable probe runs the existing vulnerable, protected, paired benign,
and blanket-block Ticket controls at one deterministic seed. Its report records
the controller-derived first divergence and exact structured tool arguments and
results. The report keeps `declared_hypothesis` separate from
`observed_synthetic_findings`, retains `target_observation_status=not_executed`,
and carries no target failure claim.

The approval operator is synthetic and contains no person, account, session,
credential, or payment instrument. Its grant uses the fixture's expected event
and benign fee-inclusive quote. The protected and benign replay requests pass
through the guard before `ticket.purchase.confirm`; a denied request reaches no
purchase side effect. The vulnerable target and amount observations come from
the actual synthetic bookings (`event-seoul-0815-1900-utc` and KRW 126,000), not
invented comparison values. Missing, wrong-action, wrong-target, wrong-amount,
wrong-currency, stale, and not-yet-valid controls must all be rejected before an
approval archetype is accepted.

The other eight profiles and uncovered Ticket hypotheses do not borrow a
mismatched fixture:

- Adhoc-bound legal, document, installation, and deletion surfaces remain
  `domain_replay_unavailable` until Adhoc has an oracle, protected replay,
  benign run, and blanket gate.
- Life-bound messaging and public-report actions remain
  `no_matching_declared_archetype`; the payment replay is not substituted.
- Sensitive-flow hypotheses remain `sensitive_flow_operator_unavailable` until
  an opaque, non-secret flow oracle exists.
- Research/Stock polling hypotheses remain
  `polling_budget_operator_unavailable`; their unrelated content-integrity
  faults are not presented as K-Skill evidence.

Mapping selection recomputes the metadata snapshot digest and checks its exact
path/blob/size set before authorizing a local fixture. One-input runtime wiring
remains deferred until issue #91's serialized intake envelope integrity fix is
corrected and merged. The intake's `execution_authorized=false` and
`max_experiments=0` continue to describe upstream execution; synthetic
authorization exists only on the separate mapping result.

## Metadata-only validation

`KSkillRiskProfileAdapter` accepts an injected `RepositoryEvidenceFetcher`. For
an exact registered source and skill it checks the upstream catalog manifest and
both reviewed skill paths are regular files whose blob SHA and size still match.
It does not include a default network client, so tests and offline operation use
a deterministic metadata mapping.

| Condition | Typed result |
|---|---|
| repository or commit differs | `catalog_revision_not_registered` |
| skill is outside the reviewed shortlist | `unknown_skill` |
| reviewed path is absent | `catalog_path_missing` |
| blob SHA or size differs | `catalog_metadata_drift` |
| object/path policy differs | `catalog_metadata_policy_rejected` |
| metadata provider is unavailable | `catalog_metadata_unavailable` |

All unsupported results contain zero experiments and zero findings. Partial
metadata gathered before an error is discarded.

## Rebuilding the snapshot

GitHub's Git commit endpoint binds a commit SHA to its root tree. The recursive
tree endpoint returns path, object type, blob SHA, and size, and marks an
incomplete response with `truncated`. The verifier also hashes the downloaded
upstream plugin manifest as a Git blob and parses its sorted skill list. It
rejects unrelated commit/tree pairs, catalog membership drift, truncated trees,
malformed entries, duplicate paths, missing reviewed paths, and invalid SHAs. It
performs no network request and writes only to stdout.

`tests/fixtures/k_skill_plugin_v1.json` is the byte-identical 3,837-byte upstream
manifest used by offline tests; its Git blob ID is asserted as
`6d9a5767b057566cf69977c291d95c3bb374961f`.

```bash
gh api "repos/NomaDamas/k-skill/git/commits/06d017ac05317da31ab2c8d6a9accf4ad4db70ad" \
  > /tmp/k-skill-commit.json
gh api "repos/NomaDamas/k-skill/git/trees/e7d5ac2fb2984cb8c81c28d07ce4eceaa020bbb7?recursive=1" \
  > /tmp/k-skill-tree.json
gh api -H "Accept: application/vnd.github.raw+json" \
  "repos/NomaDamas/k-skill/contents/.claude-plugin/plugin.json?ref=06d017ac05317da31ab2c8d6a9accf4ad4db70ad" \
  > /tmp/k-skill-plugin.json
PYTHONPATH=src python scripts/snapshot-k-skill.py \
  --tree /tmp/k-skill-tree.json \
  --commit-metadata /tmp/k-skill-commit.json \
  --upstream-catalog /tmp/k-skill-plugin.json \
  > /tmp/k-skill-catalog.candidate.json
```

The verifier accepts only the already-reviewed source pin. A new commit requires
a new catalog schema/version and human review of changed `skill.json`,
`instruction.md`, and tool/risk classifications; this command deliberately
rejects automatic carry-over. Review the candidate diff before replacing the
checked-in fixture.

The tree response contract and truncation limits are documented in the
[GitHub Git Trees API](https://docs.github.com/en/rest/git/trees?apiVersion=2022-11-28#get-a-tree).
The immutable, extra-field-forbidden typed records use
[Pydantic model validation](https://docs.pydantic.dev/latest/concepts/models/).

## Verification

```bash
PYTHONPATH=src python -m pytest \
  tests/unit/test_k_skill_intake.py \
  tests/unit/test_k_skill_mapping.py \
  tests/unit/test_k_skill_probes.py -q
./scripts/verify.ps1
```

The issue #61 and #115 eval entries are `k-skill-*` in
`tests/evals/cases.yaml`.
