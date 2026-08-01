"""Run the checked-in example Agent through the bounded local child runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent24.agent.sandbox_runner import LOCAL_BUNDLE_MISSION, LocalSandboxRunner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mission",
        default=LOCAL_BUNDLE_MISSION,
        help="bounded user input passed to the runner trace",
    )
    parser.add_argument("--run-id", default="local-agent-100")
    parser.add_argument("--seed", type=int, default=42)
    arguments = parser.parse_args()
    result = LocalSandboxRunner(Path(__file__).resolve().parents[1]).run(
        mission=arguments.mission,
        run_id=arguments.run_id,
        seed=arguments.seed,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.succeeded else 2


if __name__ == "__main__":
    raise SystemExit(main())
