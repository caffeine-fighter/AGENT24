"""Pinned external-Agent preflight for the one-input API workflow.

The preflight is deliberately bounded-evidence-only. It resolves a public GitHub
repository to an immutable commit, fetches one allowlisted JSON manifest at
that commit, derives an evidence-backed BehaviorProfile, routes to one domain
pack, and selects one typed P0 experiment.  Repository code is never cloned,
imported, or executed.
"""

from __future__ import annotations

import base64
import hashlib
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

from agent24.agent.external_adapters import (
    AdapterMatchError,
    UCPShoppingAdapterContract,
    adapter_for_source,
    inspect_ucp_source,
)
from agent24.agent.manifest import ALLOWED_MANIFEST_PATHS, load_manifest
from agent24.agent.mission_scope import domain_support, mission_domain, mission_scope_stop
from agent24.agent.models import (
    DomainExperimentPlan,
    ExperimentPlan,
    FaultKind,
    Mission,
    MissionFamily,
    RunResult,
    StopDecision,
)
from agent24.agent.packs import DomainKind, PackSelection, select_domain_pack
from agent24.agent.participant_intake import (
    ParticipantCompatibilityResult,
    ParticipantStaticProfiler,
    ParticipantTargetProfile,
    SourceSnapshot,
    SourceSnapshotFile,
    TargetCompatibilityReport,
    TargetPackSelection,
    build_allowlisted_adapter_compatibility,
    build_owner_manifest_compatibility,
    git_blob_sha,
    validate_static_evidence_path,
)
from agent24.agent.planner import select_p0_experiment
from agent24.agent.profile import AgentManifest, BehaviorProfile, build_behavior_profile
from agent24.agent.prompt_contract import PromptContractStatus, contract_constraints
from agent24.agent.source import (
    GitHubApiRevisionResolver,
    RevisionResolver,
    SourceDescriptor,
    resolve_source,
)

GITHUB_API = "https://api.github.com"
MAX_MANIFEST_BYTES = 256_000
MAX_ENTRYPOINT_BYTES = 64_000
MAX_CONTROLLER_SOURCE_CHARS = 24_000
DOMAIN_PLAN_SEED = 42


def _bounded_entrypoint_source(content: bytes) -> str:
    """Decode only a bounded text excerpt for the controller's source analysis."""

    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceFileFetchError("pinned owner entrypoint is not UTF-8 text") from error
    if len(source) <= MAX_CONTROLLER_SOURCE_CHARS:
        return source
    return (
        source[:MAX_CONTROLLER_SOURCE_CHARS]
        + "\n\n[controller source excerpt truncated at the bounded limit]"
    )


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


class SourceFileFetchError(ManifestFetchError):
    """The bounded owner entrypoint could not be retrieved safely."""


class ManifestFetcher(Protocol):
    """Fetch exactly one allowlisted manifest at ``source.resolved_sha``."""

    def fetch(self, source: SourceDescriptor) -> tuple[str, bytes]: ...


class SourceFileFetcher(Protocol):
    """Fetch one bounded text file named by the already-validated manifest."""

    def fetch(self, source: SourceDescriptor, path: str) -> bytes: ...


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


class GitHubContentsSourceFetcher:
    """Download one bounded entrypoint as evidence without importing it."""

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN")
        self.timeout_seconds = timeout_seconds

    def fetch(self, source: SourceDescriptor, path: str) -> bytes:
        safe_path = validate_static_evidence_path(path)
        encoded_path = quote(safe_path, safe="/")
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
                raw_response = response.read(MAX_ENTRYPOINT_BYTES * 2)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise SourceFileFetchError("pinned owner entrypoint could not be accessed") from error
        if len(raw_response) > MAX_ENTRYPOINT_BYTES * 2:
            raise SourceFileFetchError("pinned owner entrypoint response is oversized")
        try:
            payload = json.loads(raw_response.decode("utf-8"))
            encoded = payload["content"]
            if payload.get("encoding") != "base64" or not isinstance(encoded, str):
                raise ValueError("unexpected contents encoding")
            content = base64.b64decode("".join(encoded.split()), validate=True)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SourceFileFetchError("pinned owner entrypoint response is malformed") from error
        if len(content) > MAX_ENTRYPOINT_BYTES:
            raise SourceFileFetchError("pinned owner entrypoint exceeds the bounded intake limit")
        return content


class MappingSourceFileFetcher:
    """Network-free bounded source fixture keyed by relative path."""

    def __init__(self, files: Mapping[str, bytes | str]) -> None:
        self.files = {
            validate_static_evidence_path(path): (
                value.encode("utf-8") if isinstance(value, str) else bytes(value)
            )
            for path, value in files.items()
        }

    def fetch(self, source: SourceDescriptor, path: str) -> bytes:
        del source
        safe_path = validate_static_evidence_path(path)
        try:
            content = self.files[safe_path]
        except KeyError as error:
            raise SourceFileFetchError("fixture has no bounded owner entrypoint") from error
        if len(content) > MAX_ENTRYPOINT_BYTES:
            raise SourceFileFetchError("fixture owner entrypoint is oversized")
        return content


class ExternalIntakeResult(BaseModel):
    """Pinned source and manifest artifacts, before any plan is constructed."""

    model_config = ConfigDict(frozen=True)

    source: SourceDescriptor
    source_snapshot: SourceSnapshot
    target_profile: ParticipantTargetProfile
    compatibility_selection: TargetPackSelection
    compatibility_report: TargetCompatibilityReport
    manifest: AgentManifest
    mission: Mission
    profile: BehaviorProfile
    adapter_contract: UCPShoppingAdapterContract | None = None
    entrypoint_source: str | None = None


class ExternalPreflightResult(BaseModel):
    """Auditable artifacts produced after observation-driven planning."""

    model_config = ConfigDict(frozen=True)

    source: SourceDescriptor
    source_snapshot: SourceSnapshot
    target_profile: ParticipantTargetProfile
    compatibility_selection: TargetPackSelection
    compatibility_report: TargetCompatibilityReport
    manifest: AgentManifest
    mission: Mission
    profile: BehaviorProfile
    pack_selection: PackSelection
    decision: DomainExperimentPlan | ExperimentPlan | StopDecision
    adapter_contract: UCPShoppingAdapterContract | None = None
    initial_observation: dict[str, object] | None = None
    entrypoint_source: str | None = None


class ExternalAgentPreflight:
    """Resolve, load, profile, and plan one external-Agent target."""

    def __init__(
        self,
        *,
        source_resolver: RevisionResolver | None = None,
        manifest_fetcher: ManifestFetcher | None = None,
        source_file_fetcher: SourceFileFetcher | None = None,
        static_profiler: ParticipantStaticProfiler | None = None,
        retrieved_at: str | None = None,
    ) -> None:
        self.source_resolver = source_resolver or GitHubApiRevisionResolver()
        self.manifest_fetcher = manifest_fetcher or GitHubContentsManifestFetcher()
        self.source_file_fetcher = source_file_fetcher
        self.static_profiler = static_profiler or ParticipantStaticProfiler()
        self.retrieved_at = retrieved_at

    def _run_allowlisted_adapter(
        self, source: SourceDescriptor, target: ExternalTarget
    ) -> ExternalPreflightResult | None:
        """Match one reviewed source and build its data-only planning artifacts."""

        if not adapter_for_source(source) or self.source_file_fetcher is None:
            return None
        entrypoint = "upsonic_shopping_agent.py"
        try:
            entrypoint_bytes = self.source_file_fetcher.fetch(source, entrypoint)
            contract = inspect_ucp_source(
                source,
                path=entrypoint,
                content=entrypoint_bytes,
            )
        except AdapterMatchError as error:
            raise SourceFileFetchError("allowlisted adapter source contract mismatch") from error

        manifest = contract.manifest(source)
        compatibility = build_allowlisted_adapter_compatibility(
            source,
            manifest,
            entrypoint_path=contract.entrypoint,
            entrypoint_bytes=entrypoint_bytes,
            adapter_version=contract.adapter_id,
        )
        mission = Mission(
            text=target.mission,
            family=manifest.mission_family,
            # ``run_intake`` is synchronous and may also be used by offline
            # callers.  Seed the mission with the typed deterministic
            # projection; the API runtime may replace its source with Luna
            # before any target observation or side effect is allowed.
            constraints=contract_constraints(
                target.mission,
                permissions=manifest.permissions,
            ),
        )
        profile = build_behavior_profile(manifest, baseline=None)
        pack_selection = select_domain_pack(
            manifest=manifest,
            profile=profile,
            mission=mission,
        )
        decision: ExperimentPlan | StopDecision
        if pack_selection.stop is not None:
            decision = pack_selection.stop
        else:
            decision = select_p0_experiment(profile, mission)
        return ExternalPreflightResult(
            source=source,
            source_snapshot=compatibility.source_snapshot,
            target_profile=compatibility.target_profile,
            compatibility_selection=compatibility.pack_selection,
            compatibility_report=compatibility.compatibility_report,
            manifest=manifest,
            mission=mission,
            profile=profile,
            pack_selection=pack_selection,
            decision=decision,
            adapter_contract=contract,
            entrypoint_source=_bounded_entrypoint_source(entrypoint_bytes),
        )

    def run(
        self, target: ExternalTarget
    ) -> ExternalPreflightResult | ParticipantCompatibilityResult:
        intake = self.run_intake(target)
        if isinstance(intake, (ExternalPreflightResult, ParticipantCompatibilityResult)):
            return intake
        return self.finalize(intake)

    def run_intake(
        self, target: ExternalTarget
    ) -> ExternalIntakeResult | ExternalPreflightResult | ParticipantCompatibilityResult:
        """Resolve and inspect the target without choosing an experiment.

        The local reviewed bundle uses this split to execute the submitted Agent
        once before a domain pack, fault, or experiment plan exists.  Existing
        metadata-only and allowlisted-adapter paths keep their historical
        terminal behavior when no owner manifest is available.
        """

        source = resolve_source(
            target.repository_url,
            ref=target.requested_ref,
            resolver=self.source_resolver,
            retrieved_at=self.retrieved_at,
        )
        try:
            manifest_path, manifest_bytes = self.manifest_fetcher.fetch(source)
        except ManifestUnavailableError:
            adapter_result = self._run_allowlisted_adapter(source, target)
            if adapter_result is not None:
                return adapter_result
            return self.static_profiler.profile(source)
        with tempfile.TemporaryDirectory(prefix="agent24-manifest-") as temporary_root:
            root = Path(temporary_root)
            path = root / manifest_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(manifest_bytes)
            manifest = load_manifest(root, source, manifest_path=manifest_path)

        downloaded_files: tuple[SourceSnapshotFile, ...] = ()
        entrypoint_source: str | None = None
        if self.source_file_fetcher is not None and manifest.entrypoint != manifest_path:
            entrypoint_bytes = self.source_file_fetcher.fetch(source, manifest.entrypoint)
            entrypoint_source = _bounded_entrypoint_source(entrypoint_bytes)
            downloaded_files = (
                SourceSnapshotFile(
                    path=manifest.entrypoint,
                    size=len(entrypoint_bytes),
                    blob_sha=git_blob_sha(entrypoint_bytes),
                    content_sha256=f"sha256:{hashlib.sha256(entrypoint_bytes).hexdigest()}",
                    retrieval_mode="bounded_download",
                ),
            )

        compatibility = build_owner_manifest_compatibility(
            source,
            manifest,
            manifest_path=manifest_path,
            manifest_bytes=manifest_bytes,
            downloaded_files=downloaded_files,
        )

        mission = Mission(
            text=target.mission,
            family=manifest.mission_family,
            # Keep synchronous callers safe with the same deterministic
            # projection used by the adapter path.  The API runtime may
            # replace the source with Luna before any target observation, but
            # it must never start from fixed one-purchase semantics.
            constraints=contract_constraints(
                target.mission,
                permissions=manifest.permissions,
            ),
        )
        profile = build_behavior_profile(manifest, baseline=None)

        return ExternalIntakeResult(
            source=source,
            source_snapshot=compatibility.source_snapshot,
            target_profile=compatibility.target_profile,
            compatibility_selection=compatibility.pack_selection,
            compatibility_report=compatibility.compatibility_report,
            manifest=manifest,
            mission=mission,
            profile=profile,
            adapter_contract=None,
            entrypoint_source=entrypoint_source,
        )

    def finalize(
        self,
        intake: ExternalIntakeResult,
        *,
        baseline: RunResult | None = None,
    ) -> ExternalPreflightResult:
        """Build the pack and experiment plan after optional AUT observation."""

        profile = build_behavior_profile(intake.manifest, baseline=baseline)
        mission = intake.mission

        # Which gym before which fault.  ``select_p0_experiment`` only knows the
        # Life-v0 operator table, so asking it about a Research or Stock agent
        # would produce an honest-looking "unsupported" for the wrong reason.
        # The router names the domain first, and only the Life pack continues
        # into the operator selection below.
        pack_selection = select_domain_pack(
            manifest=intake.manifest, profile=profile, mission=mission
        )
        # Which gym, then whether the submitted mission is inside that gym's
        # scope, then which fault.  The scope check runs after routing and never
        # rewrites the selection: the pack really is executable, and folding
        # this into the router would make ``selection_digest`` depend on prose.
        # A routing stop that already fired keeps its own reason.
        scope_stop = mission_scope_stop(mission, pack_selection)
        decision: DomainExperimentPlan | ExperimentPlan | StopDecision
        if pack_selection.stop is not None:
            decision = pack_selection.stop
        elif scope_stop is not None:
            decision = scope_stop
        elif (
            isinstance(mission.constraints.get("prompt_contract"), Mapping)
            and mission.constraints["prompt_contract"].get("status")
            == PromptContractStatus.PERMISSION_CONFLICT.value
        ):
            conflicts = mission.constraints["prompt_contract"].get(
                "permission_conflicts", []
            )
            detail = "; ".join(str(item) for item in conflicts)[:500]
            decision = StopDecision(
                stop=True,
                reason="unsupported_input",
                detail=(
                    "prompt contract exceeds the manifest permission ceiling"
                    + (f": {detail}" if detail else "")
                ),
            )
        else:
            # Once the scope gate says a documented failure domain is
            # stageable, run only the fault family that earned that verdict.
            # The router and its digest stay untouched; this prevents a
            # supported communication request from silently becoming the
            # pack's higher-priority payment experiment.
            planning_mission = mission
            allowed_faults = None
            domain = mission_domain(mission.text)
            selected = pack_selection.selected
            if selected is not None and selected.domain_kind in {
                DomainKind.RESEARCH,
                DomainKind.STOCK,
            }:
                fixture_id = (
                    "research.full-gauntlet.v1"
                    if selected.domain_kind is DomainKind.RESEARCH
                    else "stock.full-gauntlet.v1"
                )
                decision = DomainExperimentPlan(
                    plan_id=(
                        f"{selected.pack_id}:target-gym:{fixture_id}:"
                        f"seed-{DOMAIN_PLAN_SEED}"
                    ),
                    pack_id=selected.pack_id,
                    domain=(
                        "research"
                        if selected.domain_kind is DomainKind.RESEARCH
                        else "stock"
                    ),
                    fixture_id=fixture_id,
                    seed=DOMAIN_PLAN_SEED,
                    tool_names=tuple(
                        sorted({*selected.matched_anchors, *selected.matched_optional})
                    ),
                    max_tool_calls=pack_selection.budget.max_tool_calls
                    if pack_selection.budget is not None
                    else 1,
                    mission=mission.text,
                    rationale=(
                        "manifest tool surface routed to the matching read-only Gym; "
                        "the target Agent receives only this selected surface"
                    ),
                )
            else:
                if domain is not None and selected is not None:
                    observed = (*selected.matched_anchors, *selected.matched_optional)
                    support = domain_support(domain, tools=observed)
                    if support.supported and support.fault_family:
                        if domain.value == "communication":
                            planning_mission = mission.model_copy(
                                update={"family": MissionFamily.EMAIL}
                            )
                        allowed_faults = frozenset({FaultKind(support.fault_family)})
                decision = select_p0_experiment(
                    profile,
                    planning_mission,
                    allowed_faults=allowed_faults,
                )
                mission = planning_mission

        return ExternalPreflightResult(
            source=intake.source,
            source_snapshot=intake.source_snapshot,
            target_profile=intake.target_profile,
            compatibility_selection=intake.compatibility_selection,
            compatibility_report=intake.compatibility_report,
            manifest=intake.manifest,
            mission=mission,
            profile=profile,
            pack_selection=pack_selection,
            decision=decision,
            adapter_contract=intake.adapter_contract,
            entrypoint_source=intake.entrypoint_source,
        )


__all__ = [
    "GITHUB_API",
    "MAX_MANIFEST_BYTES",
    "ExternalAgentPreflight",
    "ExternalIntakeResult",
    "ExternalPreflightResult",
    "ExternalTarget",
    "GitHubContentsManifestFetcher",
    "GitHubContentsSourceFetcher",
    "ManifestFetchError",
    "ManifestFetcher",
    "ManifestResponseError",
    "ManifestUnavailableError",
    "MappingManifestFetcher",
    "MappingSourceFileFetcher",
    "SourceFileFetchError",
    "SourceFileFetcher",
    "ParticipantCompatibilityResult",
]
