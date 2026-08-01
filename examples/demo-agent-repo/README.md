# AGENT24 local demo Agent

This directory is a repository-shaped, standalone local bundle inside AGENT24.
It is not a second public GitHub repository, and it does not use fake GitHub
coordinates. The bundle has its own `pyproject.toml`, manifest, source module,
and tests; its runtime dependencies are empty and its source never imports the
parent project.

The participant contract is one mission plus the exact SandboxGym surface:

- `catalog.search(query, max_price_krw)`
- `payment.charge(product_id, quantity, idempotency_key)`
- `payment.status(payment_id, idempotency_key)`
- `calendar.create(title, start_at, end_at, timezone, idempotency_key)`

The manifest uses dotted conceptual names. The standalone Python protocol uses
underscore names only because the Agents SDK wrapper grammar requires them; the
argument object sent by the Agent always contains every declared field,
including nullable/defaulted fields. Missing or extra response fields fail
closed.

## Deliberate defect

`ExampleCakeAgent` is the same code in every test harness. When the first
`payment.charge` returns `status: "unknown"`, it retries exactly once without
calling `payment.status` first. That retry-without-reconciliation is the one
deliberate defect. Clean, vulnerable, and protected harnesses change only the
deterministic tool responses/policy boundary; they do not fork the Agent or
manifest. The protected harness records the retry as a tool call but returns a
committed idempotent/policy replay, so the ledger still contains one payment
effect and the mission completes.

## Real versus synthetic boundary

The parent preflight reads only `.agent24/manifest.json` and the allowlisted
entrypoint as bounded evidence. It records the local source kind/resolver, the
deterministic SHA-256 bundle revision over the allowlisted paths and bytes, each
path, Git blob SHA, and per-file SHA-256 content hash. The bundle digest is
marked `bundle_sha256`, never presented as a Git commit.

The parent demo runs the corresponding network-disabled synthetic SandboxGym.
It does not import or execute this entrypoint, call OpenAI, access GitHub, or
call real payment/calendar APIs. The local source target is:

```json
{
  "repository_url": "local://agent24/examples/demo-agent-repo",
  "requested_ref": "<64-hex bundle SHA-256 printed by demo-local.py>",
  "mission": "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문하고 가족 캘린더에도 일정을 등록해줘."
}
```

The separate #100 runner vertical slice executes this exact reviewed bundle
locally through a stdlib-only `python -I -S` child. The host copies only the
verified manifest and entrypoint into a read-only temporary source tree; the
child can request only the four host-dispatched SandboxGym tools. It does not
receive the fixture seed, fault metadata, oracle, ledger, controller state, or
host environment. This is a bounded demo runner, not an arbitrary-code
security certification. Because this participant is a single-mission demo,
the runner also rejects any mission other than the order-plus-calendar contract
above before starting the child.

From the AGENT24 root:

```bash
uv run python scripts/run-local-agent.py
```

The command prints the controller-owned JSON evidence and writes no run log.

## Exact standalone commands

From this directory:

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check src tests
```

The `dev` group installs only test/lint tooling. The Agent itself has no
runtime package dependency. To run the parent browser rehearsal from the AGENT24
root:

```bash
uv run python scripts/demo-local.py --example-agent --port 8769
```

Then open `http://127.0.0.1:8769/index.html?demo=example-agent`. This is local
synthetic evidence only. Creating or publishing a separate public repository is
explicitly outside this demo contract.
