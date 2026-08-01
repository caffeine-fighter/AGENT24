# NIGHTMARE LAB hosted demo

> Implementation snapshot: the ratified D3 request, event-origin, terminal, and
> reconnect replacement is [`../web/event-contract.md`](../web/event-contract.md).
> The hosted shapes below remain A4 migration evidence, not a second contract.

This directory packages the existing NIGHTMARE LAB interface for Sites. It keeps the browser experience intact and adds a Cloudflare Worker-compatible API surface:

- `POST /api/runs` resolves a requested public GitHub ref and asks the OpenAI Responses API for a bounded experiment rationale.
- `GET /api/runs/:runId/events` emits the autonomous CLONE → CRASH → AUTOPSY → VACCINE → REPLAY trace over SSE.
- `/health` reports configuration without returning credentials.

All world changes are synthetic. Repository source and real side-effect tools are never executed. If OpenAI or GitHub is unavailable, the same path declares `offline_demo` and uses the deterministic fallback.

## Prerequisites

- Node.js `>=22.13.0`

## Quick start

```bash
npm ci
npm run typecheck
npm run dev
npm run build
```

Open [http://localhost:3000](http://localhost:3000). Runtime variables are documented in `.env.example`; never expose them to browser code.

`RUN_CONTEXT_SECRET` must be a stable random secret in hosted production. It encrypts the short-lived context that binds the accepted `run_id` to the observed source SHA, OpenAI response ID, execution mode, and mission. Local development can use an isolate-local ephemeral key; hosted deployments must configure the secret so a follow-up SSE request can be verified on any Edge isolate.

The pinned `sharp` and `postcss` overrides keep Next's optional image path and CSS toolchain on patched releases. `node-addon-api` and `node-gyp` preserve clean installs on development hosts where `sharp` detects a system libvips and selects its source-build fallback.

## Deployment smoke

After `OPENAI_API_KEY` is configured as a production secret, verify the real hosted path from the repository root:

```bash
python scripts/hosted-smoke.py
```

The command requires `openai_hosted`, a pinned 40-character source SHA, all five phases, 34 contiguous SSE events, an OpenAI `resp_…` evidence ID, and a verified `SIMULATION_ONLY` terminal event. The P0 submission contract accepts public GitHub source only; the build commit reported by `/health` is deployment provenance and is never substituted for submitted-source preflight. The command prints no credential values.

For an owner-only Sites deployment, place only the bypass token in the process environment as `SITES_BYPASS_TOKEN`; the script sends it as `OAI-Sites-Authorization: Bearer …`. Never pass it on the command line or commit it. The smoke also proves that token tampering, evidence query injection, and cross-run reuse stop before SSE. To rehearse the explicit fallback without a private GitHub token, use a public source: `--base-url http://localhost:3000 --expect-mode offline_demo --repository https://github.com/openai/openai-python --allow-ephemeral-context`.
