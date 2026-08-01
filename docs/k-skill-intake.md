# K-Skill source-intake and risk-profile contract

This document covers the source-intake/provenance slice of issue #61. It does
not implement the synthetic Gym mapping owned by the issue's other worker.

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
“failure observed,” or “safe.” The separate synthetic-mapping worker must make
any executable DomainPack decision and preserve this claim boundary.

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
PYTHONPATH=src python -m pytest tests/unit/test_k_skill_intake.py -q
./scripts/verify.ps1
```

The issue #61 eval entries are `k-skill-*` in `tests/evals/cases.yaml`.
