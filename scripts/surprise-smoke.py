"""Run the five AGENT:24 Surprise Task rehearsals against the demo server."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent24.evals import SURPRISE_CASES, SurpriseRunReport, run_http_case


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    selected = [
        case for case in SURPRISE_CASES if not args.case_ids or case.case_id in args.case_ids
    ]
    unknown = set(args.case_ids or ()) - {case.case_id for case in SURPRISE_CASES}
    if unknown:
        raise SystemExit(f"unknown case id: {', '.join(sorted(unknown))}")

    reports: list[SurpriseRunReport] = []
    for case in selected:
        try:
            reports.append(
                run_http_case(args.base_url, case, timeout_seconds=args.timeout_seconds)
            )
        except RuntimeError as error:
            reports.append(
                SurpriseRunReport(
                    case_id=case.case_id,
                    family=case.family,
                    mission=case.mission,
                    run_id=None,
                    mode=None,
                    passed=False,
                    errors=(str(error),),
                )
            )

    run_ids = [report.run_id for report in reports if report.run_id]
    duplicate_run_ids = len(run_ids) != len(set(run_ids))
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "passed": all(report.passed for report in reports) and not duplicate_run_ids,
        "duplicate_run_ids": duplicate_run_ids,
        "summary": {"passed": sum(report.passed for report in reports), "total": len(reports)},
        "runs": [report.model_dump(mode="json") for report in reports],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
