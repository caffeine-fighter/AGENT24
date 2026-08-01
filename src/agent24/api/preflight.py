"""Pinned external-Agent preflight for the one-input API workflow.

The preflight is deliberately metadata-only.  It resolves a public GitHub
repository to an immutable commit, fetches one allowlisted JSON manifest at
that commit, derives an evidence-backed BehaviorProfile, routes to one domain
pack, and selects one typed P0 experiment.  Repository code is never cloned,
imported, or executed.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from agent24.agent.manifest import ALLOWED_MANIFEST_PATHS, load_manifest
from agent24.agent.mission_support import classify_support, documented_mission
from agent24.agent.models import ExperimentPlan, FaultKind, Mission, MissionFamily, StopDecision
from agent24.agent.packs import PackSelection, select_domain_pack
from agent24.agent.planner import select_p0_experiment
from agent24.agent.profile import AgentManifest, BehaviorProfile, build_behavior_profile
from agent24.agent.source import (
    GitHubApiRevisionResolver,
    RevisionResolver,
    SourceDescriptor,
    resolve_source,
)

GITHUB_API = "https://api.github.com"
MAX_MANIFEST_BYTES = 256_000


class ExternalTarget(BaseModel):
    """The structured part of one user submission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository_url: str = Field(min_length=1, max_length=500)
    requested_ref: str | None = Field(default=None, max_length=200)
    mission: str = Field(min_length=1, max_length=2_000)


class ManifestFetchError(ValueError):
    """Base class for safe pinned-manifest retrieval failures."""


class ManifestUnavailableError(ManifestFetchError):
    """No allowlisted manifest exists at the resolved commit."""


class ManifestResponseError(ManifestFetchError):
    """GitHub returned malformed or oversized manifest content."""


class ManifestFetcher(Protocol):
    """Fetch exactly one allowlisted manifest at ``source.resolved_sha``."""

    def fetch(self, source: SourceDescriptor) -> tuple[str, bytes]: ...


class GitHubContentsManifestFetcher:
    """Read allowlisted manifest bytes through GitHub's contents API.

    The optional token is handed only to the HTTPS request header.  It is never
    included in an event, exception message, or stored artifact.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN")
        self.timeout_seconds = timeout_seconds

    def _fetch_path(self, source: SourceDescriptor, manifest_path: str) -> bytes | None:
        encoded_path = quote(manifest_path, safe="/")
        encoded_sha = quote(source.resolved_sha, safe="")
        request = Request(
            f"{GITHUB_API}/repos/{source.repository}/contents/{encoded_path}?ref={encoded_sha}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "agent24-nightmare-lab",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw_response = response.read(MAX_MANIFEST_BYTES * 2)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise ManifestFetchError("GitHub manifest metadata could not be accessed") from error
        except (URLError, TimeoutError, OSError) as error:
            raise ManifestFetchError("GitHub manifest metadata could not be accessed") from error

        try:
            payload = json.loads(raw_response.decode("utf-8"))
            encoded = payload["content"]
            if payload.get("encoding") != "base64" or not isinstance(encoded, str):
                raise ValueError("unexpected contents encoding")
            # GitHub line-wraps base64 content. Remove JSON-string whitespace,
            # then keep strict alphabet/padding validation for the actual data.
            normalized_encoded = "".join(encoded.split())
            manifest_bytes = base64.b64decode(normalized_encoded, validate=True)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ManifestResponseError("GitHub manifest response is malformed") from error
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ManifestResponseError("GitHub manifest exceeds the P0 size limit")
        return manifest_bytes

    def fetch(self, source: SourceDescriptor) -> tuple[str, bytes]:
        for manifest_path in ALLOWED_MANIFEST_PATHS:
            manifest_bytes = self._fetch_path(source, manifest_path)
            if manifest_bytes is not None:
                return manifest_path, manifest_bytes
        raise ManifestUnavailableError("no allowlisted manifest exists at the resolved commit")


class MappingManifestFetcher:
    """Network-free manifest fixture keyed by allowlisted relative path."""

    def __init__(self, manifests: Mapping[str, bytes | str]) -> None:
        self.manifests = {
            path: value.encode("utf-8") if isinstance(value, str) else bytes(value)
            for path, value in manifests.items()
        }

    def fetch(self, source: SourceDescriptor) -> tuple[str, bytes]:
        del source
        for manifest_path in ALLOWED_MANIFEST_PATHS:
            if manifest_path in self.manifests:
                return manifest_path, self.manifests[manifest_path]
        raise ManifestUnavailableError("fixture has no allowlisted manifest")


class ExternalPreflightResult(BaseModel):
    """Auditable artifacts produced before any AUT execution."""

    model_config = ConfigDict(frozen=True)

    source: SourceDescriptor
    manifest: AgentManifest
    mission: Mission
    profile: BehaviorProfile
    pack_selection: PackSelection
    decision: ExperimentPlan | StopDecision


class ExternalAgentPreflight:
    """Resolve, load, profile, and plan one external-Agent target."""

    def __init__(
        self,
        *,
        source_resolver: RevisionResolver | None = None,
        manifest_fetcher: ManifestFetcher | None = None,
        retrieved_at: str | None = None,
    ) -> None:
        self.source_resolver = source_resolver or GitHubApiRevisionResolver()
        self.manifest_fetcher = manifest_fetcher or GitHubContentsManifestFetcher()
        self.retrieved_at = retrieved_at

    def run(self, target: ExternalTarget) -> ExternalPreflightResult:
        source = resolve_source(
            target.repository_url,
            ref=target.requested_ref,
            resolver=self.source_resolver,
            retrieved_at=self.retrieved_at,
        )
        manifest_path, manifest_bytes = self.manifest_fetcher.fetch(source)
        with tempfile.TemporaryDirectory(prefix="agent24-manifest-") as temporary_root:
            root = Path(temporary_root)
            path = root / manifest_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(manifest_bytes)
            manifest = load_manifest(root, source, manifest_path=manifest_path)

        mission = Mission(
            text=target.mission,
            family=manifest.mission_family,
            constraints=dict(manifest.permissions),
        )
        profile = build_behavior_profile(manifest, baseline=None)

        # Which gym before which fault.  ``select_p0_experiment`` only knows the
        # Life-v0 operator table, so asking it about a Research or Stock agent
        # would produce an honest-looking "unsupported" for the wrong reason.
        # The router names the domain first, and only the Life pack continues
        # into the operator selection below.
        pack_selection = select_domain_pack(
            manifest=manifest, profile=profile, mission=mission
        )
        decision: ExperimentPlan | StopDecision
        if pack_selection.stop is not None:
            decision = pack_selection.stop
        else:
            surprise = documented_mission(target.mission)
            support = (
                classify_support(
                    surprise,
                    tools={capability.tool for capability in profile.capabilities},
                )
                if surprise is not None
                else None
            )
            if support is not None and not support.supported:
                decision = StopDecision(
                    stop=True,
                    reason=support.reason,
                    detail=support.detail,
                )
            else:
                # The pack decision above remains manifest-driven and therefore
                # auditable.  For a documented supported mission, constrain the
                # planner to the one fault family that earned that verdict so a
                # communication request cannot silently become a payment test.
                planning_mission = mission
                allowed_faults = None
                if support is not None:
                    family = (
                        MissionFamily.EMAIL
                        if support.domain.value == "communication"
                        else MissionFamily.PURCHASE
                    )
                    planning_mission = mission.model_copy(update={"family": family})
                    allowed_faults = frozenset({FaultKind(support.fault_family)})
                decision = select_p0_experiment(
                    profile,
                    planning_mission,
                    allowed_faults=allowed_faults,
                )
                mission = planning_mission

        return ExternalPreflightResult(
            source=source,
            manifest=manifest,
            mission=mission,
            profile=profile,
            pack_selection=pack_selection,
            decision=decision,
        )


__all__ = [
    "GITHUB_API",
    "MAX_MANIFEST_BYTES",
    "ExternalAgentPreflight",
    "ExternalPreflightResult",
    "ExternalTarget",
    "GitHubContentsManifestFetcher",
    "ManifestFetchError",
    "ManifestFetcher",
    "ManifestResponseError",
    "ManifestUnavailableError",
    "MappingManifestFetcher",
]
