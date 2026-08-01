# NIGHTMARE LAB hosted demo

This directory packages the existing NIGHTMARE LAB interface for Sites. It keeps the browser experience intact and adds a Cloudflare Worker-compatible API surface:

- `POST /api/runs` resolves the requested GitHub ref and asks the OpenAI Responses API for a bounded experiment rationale.
- `GET /api/runs/:runId/events` emits the autonomous CLONE → CRASH → AUTOPSY → VACCINE → REPLAY trace over SSE.
- `/health` reports configuration without returning credentials.

All world changes are synthetic. Repository source and real side-effect tools are never executed. If OpenAI or GitHub is unavailable, the same path declares `offline_demo` and uses the deterministic fallback.

## Prerequisites

- Node.js `>=22.13.0`

## Quick start

```bash
npm install
npm run dev
npm run build
```

Open [http://localhost:3000](http://localhost:3000). Runtime variables are documented in `.env.example`; never expose them to browser code.

## Deployment smoke

After `OPENAI_API_KEY` and a private-repository-capable `GITHUB_TOKEN` are configured as production secrets, verify the real hosted path from the repository root:

```bash
python scripts/hosted-smoke.py
```

The command requires `openai_hosted`, a pinned 40-character GitHub SHA, all five phases, 34 contiguous SSE events, an OpenAI `resp_…` evidence ID, and a verified `SIMULATION_ONLY` terminal event. It prints no credential values.

For an owner-only Sites deployment, place only the bypass token in the process environment as `SITES_BYPASS_TOKEN`; the script sends it as `OAI-Sites-Authorization: Bearer …`. Never pass it on the command line or commit it. To rehearse the explicit fallback without a private GitHub token, use a public source: `--base-url http://localhost:3000 --expect-mode offline_demo --repository https://github.com/openai/openai-python`.
