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

The reviewed Agent contract deliberately contains one defect: when the first
`payment.charge` returns `status: "unknown"`, the target behavior retries exactly
once without calling `payment.status` first. The live target Agent is instructed
by the reviewed manifest/runtime contract to perform this behavior, while the
standalone source child is the exact reference implementation used when the
provider is unavailable. Clean, vulnerable, and protected harnesses change only
the deterministic tool responses/policy boundary; they do not change the
manifest. The protected harness records the retry as a tool call but returns a
committed idempotent/policy replay, so the ledger still contains one payment
effect and the mission completes.

## Real versus synthetic boundary

The parent preflight reads only `.agent24/manifest.json` and the allowlisted
entrypoint as bounded evidence. It records the local source kind/resolver, the
deterministic SHA-256 bundle revision over the allowlisted paths and bytes, each
path, Git blob SHA, and per-file SHA-256 content hash. The bundle digest is
marked `bundle_sha256`, never presented as a Git commit.

The bundle is the exact source/manifest identity that the parent analyzes. When
an OpenAI provider is configured, the reviewed `ExampleCakeAgent LLM` target
runtime performs the mission as a real Agents SDK Agent and receives only the
host-owned, network-disabled local replacement SandboxGym tools. The provider
call itself stays at the host boundary; the target tools never reach GitHub or
real payment/calendar APIs. When no provider is configured, the exact source is
run in the bounded `python -I -S` child as an explicit `reference_source_child`
fallback.

```json
{
  "repository_url": "local://agent24/examples/demo-agent-repo",
  "requested_ref": "<64-hex bundle SHA-256 printed by demo-local.py>",
  "mission": "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문하고 가족 캘린더에도 일정을 등록해줘."
}
```

The reference runner copies only the verified manifest and entrypoint into a
read-only temporary source tree; the child can request only the four
host-dispatched SandboxGym tools. It does not receive the fixture seed, fault
metadata, oracle, ledger, controller state, or host environment. This is a
bounded demo runner, not an arbitrary-code security certification. Because this
participant is a single-mission demo, the runner also rejects any mission other
than the order-plus-calendar contract above before starting the child.

The parent first executes the target Agent in a host-owned observation Gym. The
Gym surface is derived from the reviewed manifest (`catalog.search`,
`payment.charge`, `payment.status`, and `calendar.create`), and the initial
`target.observation.*` stream records the Agent's actual tool calls, results,
ledger mutations, and world diff before any experiment plan exists. The ambient
`commit_then_timeout` response is observation evidence, not a preselected
experiment. If the provider is unavailable, the same event contract is produced
by the explicit source-child reference fallback.

After that observation, the parent LLM controller (default model
`gpt-5.6-luna`) receives the pinned source/manifest and bounded observation
trace. It analyzes the Agent behavior and selects only host-allowlisted
experiments through the five strict controller tools. The resulting baseline,
fault, protected replay, and minimization runs are separate
`target.execution.*` host-owned reference verifications; they are not post-hoc
claims about the initial Agent run. If no key is available, the source-child
observation and controller reference fallback are both marked explicitly.

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

Then open `http://127.0.0.1:8769/index.html?demo=example-agent`. This is actual
reviewed local target-code evidence inside the bounded sandbox; the services and
side effects remain synthetic/local. Creating or publishing a separate public
repository is explicitly outside this demo contract.
