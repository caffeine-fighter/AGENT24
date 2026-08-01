"""Run the full NIGHTMARE LAB demo locally.

By default this runs the checked-in ExampleCakeAgent entrypoint in the reviewed
bounded child sandbox. ``--live-github`` switches the source boundary to the
production metadata/content fetchers so the browser can accept a real public
GitHub path, including an allowlisted external Agent adapter. External GitHub
source remains metadata-only; only the reviewed local example is executable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import uvicorn

from agent24.agent.sandbox_runner import LocalSandboxRunner
from agent24.agent.source import (
    GitHubApiRevisionResolver,
    MappingRevisionResolver,
    SourceAccessError,
    SourceRequest,
)
from agent24.api import (
    ExternalAgentPreflight,
    GitHubContentsManifestFetcher,
    GitHubContentsSourceFetcher,
    MappingManifestFetcher,
    MappingSourceFileFetcher,
    OpenAIWhiteBoxAdapter,
    RuntimeSettings,
    create_app,
)

DEMO_REPOSITORY = "caffeine-fighter/AGENT24"
DEMO_REPOSITORY_URL = f"https://github.com/{DEMO_REPOSITORY}"
DEMO_REF = "main"
EXAMPLE_SOURCE_URL = "local://agent24/examples/demo-agent-repo"
EXAMPLE_REPOSITORY = "local/demo-agent-repo"
EXAMPLE_FIXTURE_RELATIVE_ROOT = Path("examples/demo-agent-repo")


class LocalDemoRevisionResolver(MappingRevisionResolver):
    """Resolve only the checked-out demo target and label it as local evidence."""

    name = "local-demo"

    def resolve(self, request: SourceRequest) -> str:
        if request.repository != DEMO_REPOSITORY or request.requested_ref not in {
            None,
            DEMO_REF,
        }:
            raise SourceAccessError(
                "local demo accepts only the checked-out caffeine-fighter/AGENT24@main target"
            )
        return super().resolve(request)


class LocalExampleRevisionResolver(MappingRevisionResolver):
    """Resolve only the checked-in local bundle and its SHA-256 revision."""

    name = "local-bundle"

    def resolve(self, request: SourceRequest) -> str:
        if (
            request.source_kind != "local_bundle"
            or request.source_path != str(EXAMPLE_FIXTURE_RELATIVE_ROOT)
            or request.repository != EXAMPLE_REPOSITORY
        ):
            raise SourceAccessError(
                "example demo accepts only the allowlisted local Agent bundle"
            )
        return super().resolve(request)


def _git_sha(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    sha = result.stdout.strip().lower()
    if len(sha) != 40 or any(character not in "0123456789abcdef" for character in sha):
        raise RuntimeError("the local demo checkout did not have a full Git SHA")
    return sha


def _bundle_sha256(manifest_bytes: bytes, entrypoint_bytes: bytes) -> str:
    """Hash the ordered allowlisted paths and bytes, never a Git commit."""

    digest = hashlib.sha256()
    for path, content in (
        (".agent24/manifest.json", manifest_bytes),
        ("src/example_agent.py", entrypoint_bytes),
    ):
        path_bytes = path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _example_bundle_files(repository_root: Path) -> tuple[Path, str, bytes, bytes, str]:
    fixture_root = (repository_root / EXAMPLE_FIXTURE_RELATIVE_ROOT).resolve()
    manifest_path = fixture_root / ".agent24" / "manifest.json"
    if manifest_path.is_symlink():
        raise RuntimeError("the example Agent bundle must not use symlinks")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    entrypoint = manifest["entrypoint"]
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise RuntimeError("the example Agent manifest must declare an entrypoint")
    entrypoint_path = (fixture_root / entrypoint).resolve()
    if (fixture_root / entrypoint).is_symlink():
        raise RuntimeError("the example Agent fixture must not use symlinks")
    try:
        entrypoint_path.relative_to(fixture_root)
    except ValueError as error:
        raise RuntimeError(
            "the example Agent entrypoint must stay inside its local bundle"
        ) from error
    entrypoint_bytes = entrypoint_path.read_bytes()
    return (
        fixture_root,
        entrypoint,
        manifest_bytes,
        entrypoint_bytes,
        _bundle_sha256(manifest_bytes, entrypoint_bytes),
    )


def build_app(
    repository_root: Path,
    *,
    live_github: bool = False,
    example_agent: bool = False,
    settings: RuntimeSettings | None = None,
    openai_client: Any | None = None,
):
    settings = settings or RuntimeSettings()
    if live_github:
        preflight = ExternalAgentPreflight(
            source_resolver=GitHubApiRevisionResolver(token=settings.github_token),
            manifest_fetcher=GitHubContentsManifestFetcher(token=settings.github_token),
            source_file_fetcher=GitHubContentsSourceFetcher(token=settings.github_token),
        )
        runtime = OpenAIWhiteBoxAdapter(
            settings=settings,
            preflight=preflight,
            openai_client=openai_client,
        )
        return create_app(runtime=runtime, web_root=repository_root / "web")

    if example_agent:
        _, entrypoint, manifest_bytes, entrypoint_bytes, resolved_sha = _example_bundle_files(
            repository_root
        )
        preflight = ExternalAgentPreflight(
            source_resolver=LocalExampleRevisionResolver(
                {
                    (EXAMPLE_REPOSITORY, None): resolved_sha,
                    (EXAMPLE_REPOSITORY, resolved_sha): resolved_sha,
                }
            ),
            manifest_fetcher=MappingManifestFetcher(
                {".agent24/manifest.json": manifest_bytes}
            ),
            source_file_fetcher=MappingSourceFileFetcher({entrypoint: entrypoint_bytes}),
        )
        runtime = OpenAIWhiteBoxAdapter(
            settings=settings,
            preflight=preflight,
            target_runner=LocalSandboxRunner(repository_root),
            openai_client=openai_client,
        )
        return create_app(runtime=runtime, web_root=repository_root / "web")

    manifest_path = repository_root / ".agent24" / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    entrypoint = json.loads(manifest_bytes.decode("utf-8"))["entrypoint"]
    entrypoint_path = (repository_root / entrypoint).resolve()
    if (repository_root / entrypoint).is_symlink():
        raise RuntimeError("the local demo entrypoint must not be a symlink")
    try:
        entrypoint_path.relative_to(repository_root.resolve())
    except ValueError as error:
        raise RuntimeError("the local demo entrypoint must remain inside the checkout") from error
    entrypoint_bytes = entrypoint_path.read_bytes()
    resolved_sha = _git_sha(repository_root)
    preflight = ExternalAgentPreflight(
        source_resolver=LocalDemoRevisionResolver(
            {
                (DEMO_REPOSITORY, DEMO_REF): resolved_sha,
                (DEMO_REPOSITORY, None): resolved_sha,
            }
        ),
        manifest_fetcher=MappingManifestFetcher(
            {".agent24/manifest.json": manifest_bytes}
        ),
        source_file_fetcher=MappingSourceFileFetcher({entrypoint: entrypoint_bytes}),
    )
    runtime = OpenAIWhiteBoxAdapter(
        settings=settings,
        preflight=preflight,
        openai_client=openai_client,
    )
    return create_app(runtime=runtime, web_root=repository_root / "web")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live-github",
        action="store_true",
        help="use real GitHub metadata/content fetchers for browser-submitted targets",
    )
    mode.add_argument(
        "--example-agent",
        action="store_true",
        help="explicitly use the default checked-in ExampleCakeAgent bundle",
    )
    mode.add_argument(
        "--self-target",
        action="store_true",
        help="use the checked-out AGENT24 repository itself as the analyzed target",
    )
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    example_agent = args.example_agent or not (args.live_github or args.self_target)
    if example_agent:
        _, _, manifest_bytes, entrypoint_bytes, bundle_sha = _example_bundle_files(repository_root)
        del manifest_bytes, entrypoint_bytes
        print(f"NIGHTMARE LAB local demo: {EXAMPLE_SOURCE_URL}@sha256:{bundle_sha}")
        print("SOURCE: local-bundle · checked-in standalone participant Agent")
        print("TARGET: checked-in ExampleCakeAgent entrypoint executes in a bounded child sandbox")
        print(
            "GYM: network-disabled synthetic local replacement; "
            "no real payment/calendar side effect"
        )
        print("BROWSER: /index.html · ExampleCakeAgent is the default form target")
    elif args.live_github:
        print("NIGHTMARE LAB local demo: public GitHub metadata/content intake")
        print(
            "SOURCE: GitHub revision pin + bounded manifest/entrypoint or allowlisted adapter"
        )
        print(
            "GYM: network-disabled local replacement; no submitted Python execution "
            "or real side effects"
        )
    else:
        print(f"NIGHTMARE LAB local demo: {DEMO_REPOSITORY_URL}@{DEMO_REF}")
        print("SOURCE: local-demo · current checkout SHA · manifest + entrypoint bounded intake")
        print(
            "GYM: synthetic archetype + network-disabled local replacement; "
            "no external side effects"
        )
    uvicorn.run(
        build_app(
            repository_root,
            live_github=args.live_github,
            example_agent=example_agent,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
