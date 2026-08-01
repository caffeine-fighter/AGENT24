# Eval registry

`cases.yaml` is the canonical record of what this project checks. Since issue
#107 it is also **executable**: `python -m agent24.evals` validates it and runs
it, and `scripts/verify.ps1` fails on registry drift.

Before that, nothing read this file. A duplicate id, a misspelled field, or a
pytest node renamed by an unrelated PR all stayed green — the registry described
intent it never proved. The first run of the new gate found exactly that: a case
still pointing at a test renamed two PRs earlier.

## Commands

```bash
python -m agent24.evals                    # validate + run every case
python -m agent24.evals --case planner-normal --case oracle-duplicate-payment
python -m agent24.evals --validate-only    # schema + target existence only
python -m agent24.evals --list             # case ids and their runners
```

`--validate-only` is what the canonical verifier calls: every pytest target the
registry names is already executed by the `pytest -q` step, so running them a
second time would double the gate for the same signal. Run the bare command to
execute the cases themselves.

## Runners

| runner | what it declares | what actually executes |
|---|---|---|
| `pytest` | `targets:` node ids | those nodes, via `pytest` |
| `site` | `targets:` paths under `site/` | `node --test`, when node and the site build exist |
| `gym_fixture` | `fixture_id` / `seed` / checks | loads the real fixture; runs `protected_replay` when declared |
| `runtime_events` | `input` / `mode` / `expected_event_types` | a real `POST /api/runs` run and its SSE + JSONL streams |

## Rules the schema enforces

- **Closed.** Every case model is `extra="forbid"` and the union is
  discriminated on `runner`, so an unknown field, a missing required field, or a
  field belonging to another runner fails to load.
- **Unique ids**, matching `^[a-z0-9][a-z0-9_-]*$`.
- **No shell.** Targets are lists of bare node ids. There is no command string,
  the runner uses `shell=False` with a list argv, and the node-id pattern
  refuses quotes, `;`, `&`, `|`, `$`, backticks, `..` and pytest flags.
- **No unbacked claims.** `expected_protected_state` requires the
  `protected_replay` checks that prove it.
- **Live runs name their test.** A `mode: live` case needs a mocked OpenAI
  client, which lives in the test suite, so it must bind to that node via
  `proves` rather than duplicating the stub.

## `proves`

A declarative case's `expectation` is prose. Where the harness cannot decide it,
`proves` names the pytest nodes that do, and the runner executes them. Nothing
is reported as machine-checked unless it was.

## Output

One line per case plus a summary. pytest output is captured and only a short
tail prints on failure, so a run never emits a raw log, a secret, or generated
media as an artifact. A missing prerequisite prints `[SKIP]` with its reason and
fails the run unless `--skip-unavailable` is passed; schema and target drift
always fail.
