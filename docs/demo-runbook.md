# Demo runbook

## Before presenting

- [ ] API key and quota checked without exposing the key
- [ ] Network and each external tool checked
- [ ] Clean browser session and readable font size prepared
- [ ] Main UI and Raw Stream panel visible together
- [ ] One known-good input copied locally
- [ ] Fallback input and fallback data prepared
- [ ] Local logs from rehearsal cleared

Known-good external preflight + deterministic loop를 3회 연속 확인합니다. private
repository라면 `GITHUB_TOKEN`을 환경 변수로만 주입하고 출력하거나 파일에 쓰지 않습니다.

```powershell
uv run python scripts/external-smoke.py --runs 3
```

성공 출력은 `stable: true`, full pinned `source_ref`, `verified_mitigation`,
`3/3 same-seed replays`, `sandbox_accepted: true`를 포함해야 합니다. 이는 manifest가
선택한 synthetic archetype 측정이며 제출 repository code 실행 결과가 아닙니다.

## 2 minutes — slides

1. Target user and painful moment
2. Why a self-directed agent is necessary
3. Input → decisions → tools → output architecture
4. Impact and evidence
5. Demo handoff / team (combine slides to stay within 5)

## 3 minutes — live demo

- 0:00–0:20: enter one realistic input
- 0:20–2:20: let the agent work; point to raw calls and decisions
- 2:20–2:50: show the final artifact and measurable value
- 2:50–3:00: reset to the Surprise Task input state

## 2 minutes — Surprise Task

- Repeat the same single-input path.
- Narrate only the agent's observable decisions and tool calls.
- If a dependency fails, show the designed fallback rather than editing code live.
