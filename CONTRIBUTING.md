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

## Commits

No commit may predate 2026-08-01 14:00 KST. Use imperative Conventional Commit-style messages, for example:

```text
feat(agent): add planner-to-creator handoff
fix(stream): preserve tool result payload
test(eval): add ambiguous-input case
```

