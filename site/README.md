# NIGHTMARE LAB hosted demo

This directory packages the existing NIGHTMARE LAB interface for Sites. It keeps the browser experience intact and adds a Cloudflare Worker-compatible API surface:

- `POST /api/runs` resolves a requested GitHub ref, then either downloads an allowlisted manifest plus its declared entrypoint within a bounded byte limit at the pinned SHA or selects the exact reviewed `Upsonic/UCP-Agent@3f98ef03111e560afe92347333865ccac9081d93` adapter after a bounded static contract check.
- `GET /api/runs/:runId/events` emits the autonomous CLONE → CRASH → AUTOPSY → VACCINE → REPLAY trace over SSE.
- `/health` reports configuration without returning credentials.

All world changes are synthetic. Repository source and real side-effect tools are never executed. A valid owner manifest is required before the hosted path selects the executable Life pack, except for the exact reviewed UCP adapter, which maps `complete_purchase` to a network-disabled local replacement Gym. A missing manifest on any other repository ends in zero-experiment compatibility, while an unavailable ref or preflight failure ends in an explicit source-failure terminal without running a synthetic experiment for that target.

The owner-manifest event order is `source_descriptor → source_snapshot → target_profile → pack_selection → behavior_profile → pack.selected → experiment_plan → synthetic diagnosis`; the adapter path adds `adapter.matched` immediately after `source_snapshot` and emits `gym.tool_call`/`gym.tool_result` with the reviewed UCP tool names. The hosted stream keeps the raw envelope for each step; the manifest or entrypoint path, byte count, Git blob SHA, and content digest are evidence of bounded intake only, never of repository execution.

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
python scripts/hosted-smoke.py --base-url https://your-dynamic-server.example
```

The command requires `openai_hosted`, a pinned 40-character source SHA, bounded manifest-and-entrypoint intake, owner-profile and canonical Life-pack events, all five phases, the complete contiguous SSE trace, an OpenAI `resp_…` evidence ID, and a verified `SIMULATION_ONLY` terminal event. For the reviewed public Agent path, use `--expect-adapter --expect-mode offline_demo --repository https://github.com/Upsonic/UCP-Agent --ref 3f98ef03111e560afe92347333865ccac9081d93`; this additionally requires `adapter.matched`, `network_access=disabled`, and a `complete_purchase` Gym event. The P0 submission contract accepts public GitHub source only; the build commit reported by `/health` is deployment provenance and is never substituted for submitted-source preflight. The command prints no credential values.

For an owner-only Sites deployment, place only the bypass token in the process environment as `SITES_BYPASS_TOKEN`; the script sends it as `OAI-Sites-Authorization: Bearer …`. Never pass it on the command line or commit it. The smoke also proves that token tampering, evidence query injection, and cross-run reuse stop before SSE. To rehearse the explicit fallback without a private GitHub token, use a public source: `--base-url http://localhost:3000 --expect-mode offline_demo --repository https://github.com/openai/openai-python --allow-ephemeral-context`.
To rehearse the no-manifest compatibility terminal, use a public source: `--base-url http://localhost:3000 --expect-mode compatibility_only --expect-terminal compatibility_only --expect-source pinned --repository https://github.com/openai/openai-python`.
