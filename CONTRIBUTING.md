# Contributing

## Branches

- `agent/<topic>`: orchestration and prompts
- `tools/<topic>`: tools and external integrations
- `web/<topic>`: UI and API presentation layer
- `eval/<topic>`: tests, evals, demo, and infrastructure

Keep branches short-lived. Pull before branching and rebase only your own unpublished work.

## Pull requests

- One independently reviewable change per PR.
- Include a concrete manual test or eval case.
- Attach a screenshot only when UI behavior changed.
- Explain any new secret, permission, cost, latency, or external-service dependency.
- Verify Raw API Stream behavior when orchestration or tools change.

## Review and merge governance

This section is the normative human merge policy. [`TEAM.md`](TEAM.md) remains the source for
team roles and decision rights; [`docs/verification.md`](docs/verification.md) remains the source
for canonical verification gates. GitHub does not currently enforce these merge gates, so a
checked PR-template box is not approval, current-SHA CI evidence, or an emergency authorization.

### Reviewer routing

The author cannot approve their own change. Every PR needs at least one formal GitHub approval
from an independent reviewer routed by the materially affected surface. A cross-surface change
names a reviewer for every affected surface; split the PR if that makes ownership ambiguous.

| Surface | Accountable owner from `TEAM.md` | Independent reviewer route |
|---|---|---|
| A · integration, web/site, deployment | A | B or C for execution/runtime changes; D for user-visible product or demo claims |
| B · agent intelligence, prompts, eval policy | B | C for runtime/tool contracts; A for integration/release; D for product acceptance changes |
| C · API, tools, events, source intake | C | B for agent/policy boundaries; A for integration; D for privacy, error, or evidence claims |
| D · product, UX, docs, black-box acceptance | D | A for scope and implementability; B or C when their technical contract changes |

If the listed route owner authored the change or is unavailable, another non-author owner of an
affected or adjacent surface may review. The PR records the substitution and why that reviewer is
qualified; availability alone does not permit self-approval.

### Merge eligibility

A PR is mergeable only when all of the following are true:

- it is ready for review, not a draft, and has no merge conflict;
- every review conversation is resolved;
- the routed reviewer has submitted a formal approving review;
- the approval covers the recorded head SHA and the effective diff being merged;
- canonical checks from `docs/verification.md` pass on the current merge candidate with no
  pending, failed, cancelled, silently skipped, or outdated required gate; and
- no unresolved credential, privacy, Raw API Stream, destructive-action, or product-truth risk
  remains.

Any new commit, force-push, rebase, or conflict-resolution commit after approval makes that
approval stale and requires a new review. If only the base branch moves, checks still rerun on the
current merge candidate; reapproval is required when the effective diff changes or a conflict is
resolved.

Until #70 supplies the stable canonical check and #71 has settings evidence, this policy is a
manual gate. Existing green workflow jobs do not prove that every #70 gate ran, and an unprotected
branch does not authorize bypassing review.

Enforcement snapshot (2026-08-01): the repository is private, `main` is unprotected, and the
rulesets API reports that the current plan must be upgraded or the repository made public. A must
choose that repository-level path, configure the stable #70 check with admin authority, and record
the unreviewed, stale-reviewed, and green-approved test-PR results before #71 can close. This dated
snapshot describes current capability, not a weaker policy.

### Emergency exception

An emergency exception is only for a deadline-critical release when GitHub enforcement or the
service itself is unavailable. It never converts a failed or unknown result into a pass. It may
not waive a merge conflict, a failing verification gate, an unresolved security/privacy or Raw
Stream issue, credential exposure, destructive external action, or an unknown release SHA.

Before merge, A as merge/release DRI and the affected surface owner must authorize the exception.
If either is the author or both roles are held by one person, add a second non-author surface
owner. The PR comment must record:

- exact head and base SHAs, deadline, and why the normal control is unavailable;
- the unmet control and evidence that this is infrastructure unavailability rather than a failed
  product check;
- the canonical local command, environment, exit code, and sanitized result summary;
- bounded risk, invariants that remain non-waivable, and two named authorizers;
- rollback trigger, rollback owner, and a reversible rollback procedure; and
- a follow-up issue with an expiry no later than 24 hours or the next release decision, whichever
  comes first.

After merge, rerun the unavailable checks and complete an independent post-merge audit in the
follow-up record. Revert if the exception is not cleared before expiry. An exception record is an
audited temporary deviation, never retroactive approval.

## Verification

- Run the canonical `pwsh -File ./scripts/verify.ps1` entrypoint. Until issue #70 closes, list every
  not-yet-implemented gate in the PR; after closure, a missing tool or skipped gate is a failure.
- Treat direct lint, test, build, or browser commands as diagnostics; they do not replace the
  canonical entrypoint.
- For `web/`, `site/`, runtime, fixture, or event-contract changes, include the sanitized
  normal/unsupported/fallback browser summary defined in
  [`docs/verification.md`](docs/verification.md).
- Never attach raw SSE/API items, run logs, request bodies, credentials, traces, or rendered
  HTML to CI or the PR. A UI-change screenshot must be intentionally captured, scrubbed, and
  free of Raw Stream contents, credentials, personal data, and local browser chrome.

## Commits

No commit may predate 2026-08-01 14:00 KST. Use imperative Conventional Commit-style messages, for example:

```text
feat(agent): add planner-to-creator handoff
fix(stream): preserve tool result payload
test(eval): add ambiguous-input case
```
