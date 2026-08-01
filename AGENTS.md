# Repository guidance

## Scope

This repository is a 24-hour hackathon project for the Creative Agent track.

## Invariants

- Preserve a one-input autonomous workflow: the agent owns planning, tool use, and final output.
- Preserve an unedited Raw API Stream path for tool-call and tool-result events.
- Never commit `.env`, API keys, personal data, generated media, or raw run logs.
- Keep the demo path short and deterministic; every external integration needs a failure fallback.
- Update `docs/prompt-log.md` when agent instructions materially change.
- Add or update an eval case for behavior-changing fixes.

## Working conventions

- Prefer small branches named `agent/*`, `tools/*`, `web/*`, or `eval/*`.
- Run `./scripts/verify.ps1` before opening a pull request.
- Document irreversible or architectural choices in `docs/decisions.md`.
- Do not add a second framework or datastore unless the current design cannot satisfy a demonstrated requirement.

