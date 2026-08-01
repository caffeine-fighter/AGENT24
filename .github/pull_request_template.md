## What changed

<!-- Describe the user-visible or agent-behavior change. -->

## Why

<!-- Connect the change to the product goal or judging rubric. -->

## Review and merge gate

<!-- See CONTRIBUTING.md "Review and merge governance". Checked boxes are prompts, not evidence. -->

- Surface owner(s):
- Independent reviewer(s):
- Reviewed head SHA:
- [ ] The routed reviewer submitted a formal approval for this head SHA and effective diff
- [ ] The PR is not a draft, has no conflict, and all review conversations are resolved
- [ ] Canonical checks pass on the current merge candidate with no pending, failed, skipped, or
      outdated required gate
- [ ] No emergency exception is used, or the complete record below is filled in

### Emergency exception record

<!-- Delete this subsection when no exception is used. Never paste credentials or raw run data. -->

- Deadline and unavailable control:
- Head/base SHAs:
- Local verification environment, command, exit code, and sanitized summary:
- Bounded risk and non-waivable invariants:
- Two authorizers:
- Rollback trigger, owner, and procedure:
- Follow-up issue and expiry:

## Verification

- [ ] Canonical `pwsh -File ./scripts/verify.ps1` passes; any still-open #70 gate is disclosed below
- [ ] Manual happy-path run completed
- [ ] Failure or ambiguous-input path checked
- [ ] Raw tool-call/tool-result stream checked when relevant
- [ ] Browser-affecting changes pass normal/unsupported/fallback cases and upload only the
      bounded one-day JSON summary
- [ ] No secret, personal data, generated media, raw run log, HAR, DOM, console/network dump,
      screenshot, video, trace, or rendered HTML is committed or uploaded as #70 evidence

## Demo impact

<!-- Note any change to the 3-minute demo path or Surprise Task behavior. -->
