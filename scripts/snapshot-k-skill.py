"""Rebuild K-Skill path/blob/size provenance from a downloaded Git tree.

This command never performs a network request. Fetch the Git commit object,
recursive tree, and upstream catalog manifest through a separately authenticated,
reviewable step. Output is written to stdout so replacing the reviewed catalog
always requires an explicit diff and file operation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent24.agent.k_skill_intake import (
    DEFAULT_K_SKILL_CATALOG_PATH,
    CatalogSnapshotError,
    load_k_skill_catalog,
    rebuild_k_skill_catalog_snapshot,
)

MAX_GITHUB_TREE_BYTES = 7_000_000
MAX_GITHUB_COMMIT_BYTES = 1_000_000


def _tree_payload(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    if len(raw) > MAX_GITHUB_TREE_BYTES:
        raise CatalogSnapshotError("recursive Git tree exceeds the 7 MB API limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise CatalogSnapshotError("recursive Git tree must be a JSON object")
    return payload


def _commit_payload(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    if len(raw) > MAX_GITHUB_COMMIT_BYTES:
        raise CatalogSnapshotError("Git commit metadata exceeds its byte budget")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise CatalogSnapshotError("Git commit metadata must be a JSON object")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild the metadata-only K-Skill catalog on stdout."
    )
    parser.add_argument("--tree", required=True, type=Path, help="downloaded recursive tree JSON")
    parser.add_argument(
        "--commit-metadata",
        required=True,
        type=Path,
        help="downloaded Git commit object JSON",
    )
    parser.add_argument(
        "--upstream-catalog",
        required=True,
        type=Path,
        help="downloaded .claude-plugin/plugin.json bytes",
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_K_SKILL_CATALOG_PATH)
    args = parser.parse_args()

    try:
        rebuilt = rebuild_k_skill_catalog_snapshot(
            load_k_skill_catalog(args.catalog),
            _tree_payload(args.tree),
            commit_payload=_commit_payload(args.commit_metadata),
            upstream_catalog_manifest=args.upstream_catalog.read_bytes(),
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    sys.stdout.write(f"{rebuilt.canonical_json()}\n")


if __name__ == "__main__":
    main()
