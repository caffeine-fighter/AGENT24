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
