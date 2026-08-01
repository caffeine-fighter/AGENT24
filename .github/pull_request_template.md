## What changed

<!-- Describe the user-visible or agent-behavior change. -->

## Why

<!-- Connect the change to the product goal or judging rubric. -->

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
