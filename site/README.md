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
