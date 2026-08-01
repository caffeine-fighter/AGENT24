# NIGHTMARE demo participant Agent

This directory is a small, repository-shaped participant fixture for the local
NIGHTMARE LAB demo. It is intentionally self-contained so the demo can rehearse
the full source intake contract without depending on a second public GitHub
repository.

The Agent describes a cake-order workflow and deliberately retries
`payment.charge` after an ambiguous timeout. The Lab does not import or execute
this Python file: it reads the allowlisted manifest and bounded entrypoint as
source evidence, then runs the corresponding deterministic local Gym adapter.

To rehearse it in the browser from the parent repository:

```bash
uv run python scripts/demo-local.py --example-agent --port 8769
```

Open:

```text
http://127.0.0.1:8769/index.html?demo=example-agent
```

The `example-org/nightmare-cake-agent@demo-v1` coordinates are a local fixture
alias, not a claim that this directory is already published at GitHub. The
`--live-github` mode remains available for a real public source such as the
reviewed UCP adapter.
