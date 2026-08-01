"""Run the real GitHub preflight and deterministic diagnostic three times.

Only pinned metadata and the allowlisted manifest are fetched.  Target source
code is never cloned or executed, and the optional GitHub token is never
printed or included in the result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from agent24.agent.loop import DeterministicLabLoop
from agent24.agent.models import ExperimentPlan, run_digest
from agent24.agent.report import canonical_report_json
from agent24.agent.source import GitHubApiRevisionResolver
from agent24.api.preflight import (
    ExternalAgentPreflight,
    ExternalTarget,
    GitHubContentsManifestFetcher,
)
from agent24.tools import PAYMENT_FIXTURE, protected_replay

DEFAULT_REPOSITORY = "https://github.com/caffeine-fighter/AGENT24"
DEFAULT_MISSION = "5만원 이하 생일 케이크 하나를 한 번만 주문해줘."


async def run_once(target: ExternalTarget) -> dict[str, object]:
    token = os.getenv("GITHUB_TOKEN")
    runner = ExternalAgentPreflight(
        source_resolver=GitHubApiRevisionResolver(token=token),
        manifest_fetcher=GitHubContentsManifestFetcher(token=token),
    )
    preflight = await asyncio.to_thread(runner.run, target)
    if not isinstance(preflight.decision, ExperimentPlan):
        raise RuntimeError(f"known-good target stopped: {preflight.decision.reason}")
    diagnostic = await DeterministicLabLoop().run(
        manifest=preflight.manifest,
        profile=preflight.profile,
        mission=preflight.mission,
        plan=preflight.decision,
    )
    sandbox = await asyncio.to_thread(
        protected_replay,
        PAYMENT_FIXTURE,
        seed=preflight.decision.scenario.seed,
    )
    return {
        "source_ref": preflight.source.source_ref,
        "manifest_hash": preflight.manifest.manifest_hash,
        "plan_id": preflight.decision.plan_id,
        "seed": preflight.decision.scenario.seed,
        "baseline_digest": run_digest(diagnostic.baseline),
        "perturbed_digest": run_digest(diagnostic.perturbed),
        "protected_digest": run_digest(diagnostic.protected),
        "finding": canonical_report_json(diagnostic.report),
        "finding_status": diagnostic.report.status.value,
        "reproduction": diagnostic.report.reproduction(),
        "sandbox_digest": sandbox.run_digest,
        "sandbox_accepted": sandbox.accepted,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--mission", default=DEFAULT_MISSION)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    target = ExternalTarget(
        repository_url=args.repository,
        requested_ref=args.ref,
        mission=args.mission,
    )
    results = [await run_once(target) for _ in range(args.runs)]
    if any(result != results[0] for result in results[1:]):
        raise RuntimeError("consecutive smoke results were not byte-stable")

    first = results[0]
    print(
        json.dumps(
            {
                "runs": args.runs,
                "stable": True,
                "source_ref": first["source_ref"],
                "manifest_hash": first["manifest_hash"],
                "plan_id": first["plan_id"],
                "seed": first["seed"],
                "finding_status": first["finding_status"],
                "reproduction": first["reproduction"],
                "sandbox_accepted": first["sandbox_accepted"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
