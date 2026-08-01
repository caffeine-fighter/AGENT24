"""Pinned, zero-execution risk intake for the public K-Skill catalog.

The adapter in this module treats upstream ``skill.json`` and ``instruction.md``
files as untrusted, reviewer-inspected metadata.  It verifies only Git tree
path/blob/size facts at one full commit SHA.  It never loads an upstream skill,
runs a script/package, opens a browser, accesses an account, or turns declared
behavior into an observed failure.

This deliberately remains separate from ``AgentManifest``, ``BehaviorProfile``
and the executable DomainPack router.  The domain and fault values below are
non-executable triage hints for the worker that owns #61's synthetic mapping.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import canonical_json as _canonical_json
from .packs import DomainKind
from .participant_intake import (
    ParticipantEvidenceAccessError,
    ParticipantEvidenceDriftError,
    ParticipantEvidenceError,
    ParticipantEvidenceMissingError,
    ParticipantEvidencePolicyError,
    RepositoryEvidenceFetcher,
    RepositoryEvidenceMetadata,
    SourceSnapshot,
    git_blob_sha,
    metadata_snapshot,
    validate_static_evidence_path,
)
from .source import SourceDescriptor

K_SKILL_REPOSITORY = "NomaDamas/k-skill"
K_SKILL_PINNED_SHA = "06d017ac05317da31ab2c8d6a9accf4ad4db70ad"
K_SKILL_PINNED_TREE_SHA = "e7d5ac2fb2984cb8c81c28d07ce4eceaa020bbb7"
K_SKILL_CATALOG_MANIFEST_PATH = ".claude-plugin/plugin.json"
K_SKILL_CATALOG_MANIFEST_BLOB_SHA = "6d9a5767b057566cf69977c291d95c3bb374961f"
K_SKILL_CATALOG_MANIFEST_SIZE = 3_837
K_SKILL_CATALOG_ENTRY_COUNT = 123
K_SKILL_CATALOG_SCHEMA = "agent24.k-skill-risk-catalog.v1"
K_SKILL_ADAPTER_VERSION = "agent24.k-skill-risk-intake.v1"
MAX_K_SKILL_CATALOG_BYTES = 256_000
MAX_K_SKILL_MANIFEST_BYTES = 64_000

K_SKILL_SHORTLIST = frozenset(
    {
        "catchtable-sniper",
        "court-payment-order-assistant",
        "express-bus-booking",
        "intercity-bus-booking",
        "iros-registry-automation",
        "k-skill-cleaner",
        "kakaotalk-mac",
        "keris-academic-search",
        "korean-stock-search",
        "ktx-booking",
        "lovebug-report",
        "popbill",
        "srt-booking",
    }
)

K_SKILL_CLAIM_BOUNDARY = (
    "LAB-REVIEWED DECLARED SURFACE ONLY: pinned public instructions were reviewed as data; "
    "no upstream skill, script, package, browser flow, account action, or synthetic experiment "
    "was executed, and no target vulnerability or safety result was observed."
)

DEFAULT_K_SKILL_CATALOG_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "k_skill_catalog_v1.json"
)

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CatalogSnapshotError(ValueError):
    """A supplied Git tree cannot produce a complete reviewed snapshot."""


class SideEffectClass(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


class RequirementLevel(StrEnum):
    REQUIRED = "required"
    CONDITIONAL = "conditional"
    NOT_REQUIRED = "not_required"
    UNKNOWN = "unknown"


class TrustBoundary(StrEnum):
    PUBLIC_NETWORK = "public_network"
    AUTHENTICATED_SESSION = "authenticated_session"
    CREDENTIAL_VAULT = "credential_vault"
    USER_APPROVAL = "user_approval"
    MANUAL_HANDOFF = "manual_handoff"
    LOCAL_FILESYSTEM = "local_filesystem"
    LEGAL_PORTAL = "legal_portal"
    PAYMENT_SURFACE = "payment_surface"
    UNTRUSTED_CONTENT = "untrusted_content"


class ProtectedAsset(StrEnum):
    CREDENTIALS = "credentials"
    ACCOUNT_SESSION = "account_session"
    PAYMENT_METHOD = "payment_method"
    RESERVATION = "reservation"
    PERSONAL_DATA = "personal_data"
    FINANCIAL_DOCUMENT = "financial_document"
    LEGAL_RECORD = "legal_record"
    LOCATION = "location"
    LOCAL_PRIVATE_DATA = "local_private_data"
    LOCAL_INSTALLATION = "local_installation"
    PUBLIC_RECORD = "public_record"
    RESEARCH_RESULTS = "research_results"
    MARKET_DATA = "market_data"


class RiskHypothesisFamily(StrEnum):
    """Source-declared risks to triage, not executable fault operators."""

    APPROVAL_SCOPE = "approval_scope"
    PARTIAL_SUCCESS_RETRY = "partial_success_retry"
    HOLD_CANCEL_RECONCILIATION = "hold_cancel_reconciliation"
    SENSITIVE_DATA_EXPOSURE = "sensitive_data_exposure"
    UNTRUSTED_CONTENT_PRIVILEGED_SINK = "untrusted_content_privileged_sink"
    POLLING_RATE_BUDGET = "polling_rate_budget"


class RiskIntakeStatus(StrEnum):
    PROFILED = "profiled"
    UNSUPPORTED = "unsupported"


class RiskIntakeReason(StrEnum):
    CATALOG_REVISION_NOT_REGISTERED = "catalog_revision_not_registered"
    UNKNOWN_SKILL = "unknown_skill"
    INVALID_SKILL_ID = "invalid_skill_id"
    CATALOG_PATH_MISSING = "catalog_path_missing"
    CATALOG_METADATA_DRIFT = "catalog_metadata_drift"
    CATALOG_METADATA_POLICY_REJECTED = "catalog_metadata_policy_rejected"
    CATALOG_METADATA_UNAVAILABLE = "catalog_metadata_unavailable"
    CATALOG_METADATA_INVALID = "catalog_metadata_invalid"


class SkillEvidenceKind(StrEnum):
    MANIFEST = "manifest"
    INSTRUCTION = "instruction"


class SkillRequirements(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    explicit_approval: RequirementLevel
    credential: RequirementLevel
    legal_boundary: RequirementLevel
    manual_handoff: RequirementLevel


def _full_sha(value: str, *, field_name: str) -> str:
    normalized = value.strip().lower()
    if not _FULL_SHA_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a full 40-character hexadecimal SHA")
    return normalized


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value


class SkillSourceEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SkillEvidenceKind
    repository: Literal["NomaDamas/k-skill"]
    commit_sha: str
    tree_sha: str
    path: str
    blob_sha: str
    size: int = Field(ge=1)

    @field_validator("commit_sha", "tree_sha", "blob_sha")
    @classmethod
    def _sha(cls, value: str, info) -> str:
        return _full_sha(value, field_name=info.field_name)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return validate_static_evidence_path(value)

    @property
    def stable_ref(self) -> str:
        return (
            f"github:{self.repository}@{self.commit_sha}:"
            f"{self.path}@{self.blob_sha}"
        )


class SkillRiskProfile(BaseModel):
    """Human-reviewed classification of behavior declared by one pinned skill."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skill_id: str
    description: str = Field(min_length=1, max_length=1_500)
    upstream_profiles: tuple[str, ...] = ()
    declared_tools: tuple[str, ...]
    declared_capabilities: tuple[str, ...]
    trust_boundaries: tuple[TrustBoundary, ...]
    protected_assets: tuple[ProtectedAsset, ...]
    side_effect: SideEffectClass
    requirements: SkillRequirements
    applicable_domains: tuple[DomainKind, ...]
    fault_hypotheses: tuple[RiskHypothesisFamily, ...] = ()
    evidence: tuple[SkillSourceEvidence, ...]
    rationale: str = Field(min_length=1, max_length=800)
    assessment_kind: Literal["declared_surface"] = "declared_surface"
    observation_status: Literal["not_executed"] = "not_executed"
    claim_boundary: str = K_SKILL_CLAIM_BOUNDARY

    @field_validator("skill_id")
    @classmethod
    def _skill_id(cls, value: str) -> str:
        if not _SKILL_ID_RE.fullmatch(value):
            raise ValueError("skill_id must be a canonical lowercase slug")
        return value

    @field_validator("declared_tools")
    @classmethod
    def _declared_tool_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(not _TOOL_ID_RE.fullmatch(value) for value in values):
            raise ValueError("declared_tools must contain normalized tool identifiers")
        return values

    @field_validator("claim_boundary")
    @classmethod
    def _claim_boundary_is_fixed(cls, value: str) -> str:
        if value != K_SKILL_CLAIM_BOUNDARY:
            raise ValueError("claim_boundary must preserve the declared-surface boundary")
        return value

    @model_validator(mode="after")
    def _evidence_and_collections_are_unambiguous(self) -> Self:
        collections = {
            "upstream_profiles": self.upstream_profiles,
            "declared_tools": self.declared_tools,
            "declared_capabilities": self.declared_capabilities,
            "trust_boundaries": self.trust_boundaries,
            "protected_assets": self.protected_assets,
            "applicable_domains": self.applicable_domains,
            "fault_hypotheses": self.fault_hypotheses,
        }
        for field_name, values in collections.items():
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} values must be unique")
        if not self.declared_capabilities or not self.applicable_domains:
            raise ValueError("profiles require capabilities and a non-executable domain hint")
        expected_paths = {
            SkillEvidenceKind.MANIFEST: f"{self.skill_id}/skill.json",
            SkillEvidenceKind.INSTRUCTION: f"{self.skill_id}/instruction.md",
        }
        if tuple(item.kind for item in self.evidence) != (
            SkillEvidenceKind.MANIFEST,
            SkillEvidenceKind.INSTRUCTION,
        ):
            raise ValueError("evidence must contain manifest then instruction metadata")
        if any(item.path != expected_paths[item.kind] for item in self.evidence):
            raise ValueError("evidence path must match its reviewed skill and kind")
        if (
            self.side_effect is SideEffectClass.IRREVERSIBLE
            and self.requirements.explicit_approval is not RequirementLevel.REQUIRED
        ):
            raise ValueError("irreversible declared surfaces require explicit approval")
        return self


class KSkillCatalogManifestEvidence(BaseModel):
    """Pinned identity of the upstream list that makes a directory a catalog member."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Literal[".claude-plugin/plugin.json"] = K_SKILL_CATALOG_MANIFEST_PATH
    blob_sha: str
    size: int = Field(ge=1, le=MAX_K_SKILL_MANIFEST_BYTES)
    entry_count: int = Field(ge=1)

    @field_validator("blob_sha")
    @classmethod
    def _blob_sha(cls, value: str) -> str:
        return _full_sha(value, field_name="catalog manifest blob_sha")


class KSkillCatalogSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: Literal["NomaDamas/k-skill"]
    commit_sha: str
    tree_sha: str
    committed_at: str
    commit_url: str
    catalog_manifest: KSkillCatalogManifestEvidence

    @field_validator("commit_sha", "tree_sha")
    @classmethod
    def _sha(cls, value: str, info) -> str:
        return _full_sha(value, field_name=info.field_name)

    @field_validator("committed_at")
    @classmethod
    def _committed_at(cls, value: str) -> str:
        return _timestamp(value)

    @model_validator(mode="after")
    def _canonical_commit_url(self) -> Self:
        expected = f"https://github.com/{self.repository}/commit/{self.commit_sha}"
        if self.commit_url != expected:
            raise ValueError("commit_url must identify the exact pinned commit")
        return self


def _catalog_digest(payload: Mapping[str, object]) -> str:
    stable = dict(payload)
    stable.pop("catalog_digest", None)
    raw_profiles = stable.get("profiles")
    if isinstance(raw_profiles, (list, tuple)):
        normalized_profiles: list[object] = []
        for profile in raw_profiles:
            if not isinstance(profile, Mapping):
                normalized_profiles.append(profile)
                continue
            normalized = dict(profile)
            normalized.setdefault("claim_boundary", K_SKILL_CLAIM_BOUNDARY)
            normalized_profiles.append(normalized)
        stable["profiles"] = normalized_profiles
    digest = hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class KSkillCatalogSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["agent24.k-skill-risk-catalog.v1"]
    source: KSkillCatalogSource
    reviewed_at: str
    profiles: tuple[SkillRiskProfile, ...]
    catalog_digest: str

    @field_validator("reviewed_at")
    @classmethod
    def _reviewed_at(cls, value: str) -> str:
        return _timestamp(value)

    @field_validator("catalog_digest")
    @classmethod
    def _digest_shape(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _DIGEST_RE.fullmatch(normalized):
            raise ValueError("catalog_digest must use sha256:<64 hex> form")
        return normalized

    @model_validator(mode="after")
    def _complete_stable_snapshot(self) -> Self:
        names = tuple(item.skill_id for item in self.profiles)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("catalog profiles must be sorted by unique skill_id")
        if frozenset(names) != K_SKILL_SHORTLIST:
            raise ValueError("catalog profiles must cover the reviewed P0 shortlist")
        for profile in self.profiles:
            for evidence in profile.evidence:
                if (
                    evidence.repository != self.source.repository
                    or evidence.commit_sha != self.source.commit_sha
                    or evidence.tree_sha != self.source.tree_sha
                ):
                    raise ValueError("profile evidence must use the catalog source pin")
        expected = _catalog_digest(self.model_dump(mode="json"))
        if self.catalog_digest != expected:
            raise ValueError("catalog_digest does not match the canonical snapshot")
        return self

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))

    def profile(self, skill_id: str) -> SkillRiskProfile:
        for profile in self.profiles:
            if profile.skill_id == skill_id:
                return profile
        raise KeyError(skill_id)


class SkillRiskRegistry:
    """Exact-source lookup; it does not route or authorize a synthetic run."""

    def __init__(self, catalog: KSkillCatalogSnapshot) -> None:
        self.catalog = catalog
        self._profiles = {profile.skill_id: profile for profile in catalog.profiles}

    def get(self, source: SourceDescriptor, skill_id: str) -> SkillRiskProfile | None:
        if (
            source.repository != self.catalog.source.repository
            or source.resolved_sha != self.catalog.source.commit_sha
        ):
            return None
        return self._profiles.get(skill_id)


class KSkillIntakeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: RiskIntakeStatus
    reason: RiskIntakeReason | None = None
    source_ref: str
    skill_id: str
    profile: SkillRiskProfile | None = None
    source_snapshot: SourceSnapshot
    execution_authorized: Literal[False] = False
    max_experiments: Literal[0] = 0
    failure_claims: tuple[str, ...] = ()
    observation_status: Literal["not_executed"] = "not_executed"
    adapter_version: Literal["agent24.k-skill-risk-intake.v1"] = K_SKILL_ADAPTER_VERSION
    claim_boundary: str = K_SKILL_CLAIM_BOUNDARY

    @field_validator("skill_id")
    @classmethod
    def _bounded_skill_id(cls, value: str) -> str:
        if not _SKILL_ID_RE.fullmatch(value):
            raise ValueError("result skill_id must be a canonical lowercase slug")
        return value

    @field_validator("claim_boundary")
    @classmethod
    def _claim_boundary_is_fixed(cls, value: str) -> str:
        if value != K_SKILL_CLAIM_BOUNDARY:
            raise ValueError("claim_boundary must preserve the declared-surface boundary")
        return value

    @model_validator(mode="after")
    def _no_execution_or_failure_claim(self) -> Self:
        if self.failure_claims:
            raise ValueError("declared-surface intake cannot contain failure claims")
        if self.status is RiskIntakeStatus.PROFILED:
            if self.profile is None or self.reason is not None:
                raise ValueError("profiled results require a profile and no error reason")
        elif self.profile is not None or not self.reason:
            raise ValueError("unsupported results require a reason and no profile")
        return self


def _empty_snapshot(source: SourceDescriptor) -> SourceSnapshot:
    payload: dict[str, object] = {
        "source_ref": source.source_ref,
        "mode": "metadata_only",
        "files": [],
        "total_bytes": 0,
        "execution_scope": "none",
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return SourceSnapshot(
        source_ref=source.source_ref,
        mode="metadata_only",
        files=(),
        total_bytes=0,
        execution_scope="none",
        snapshot_digest=f"sha256:{digest}",
    )


class KSkillRiskProfileAdapter:
    """Verify the checked-in declaration profile using metadata-only evidence."""

    def __init__(
        self,
        *,
        catalog: KSkillCatalogSnapshot,
        evidence_fetcher: RepositoryEvidenceFetcher,
    ) -> None:
        self.catalog = catalog
        self.registry = SkillRiskRegistry(catalog)
        self.evidence_fetcher = evidence_fetcher

    def _unsupported(
        self,
        source: SourceDescriptor,
        skill_id: str,
        reason: RiskIntakeReason,
    ) -> KSkillIntakeResult:
        return KSkillIntakeResult(
            status=RiskIntakeStatus.UNSUPPORTED,
            reason=reason,
            source_ref=source.source_ref,
            skill_id=skill_id,
            source_snapshot=_empty_snapshot(source),
        )

    def assess(self, source: SourceDescriptor, skill_id: str) -> KSkillIntakeResult:
        if not isinstance(skill_id, str) or not _SKILL_ID_RE.fullmatch(skill_id):
            return self._unsupported(
                source,
                "invalid-skill-id",
                RiskIntakeReason.INVALID_SKILL_ID,
            )
        if (
            source.repository != self.catalog.source.repository
            or source.resolved_sha != self.catalog.source.commit_sha
        ):
            return self._unsupported(
                source,
                skill_id,
                RiskIntakeReason.CATALOG_REVISION_NOT_REGISTERED,
            )
        profile = self.registry.get(source, skill_id)
        if profile is None:
            return self._unsupported(source, skill_id, RiskIntakeReason.UNKNOWN_SKILL)

        observed: list[RepositoryEvidenceMetadata] = []
        try:
            catalog_manifest = self.catalog.source.catalog_manifest
            expected_metadata = (
                (
                    catalog_manifest.path,
                    catalog_manifest.blob_sha,
                    catalog_manifest.size,
                ),
                *(
                    (item.path, item.blob_sha, item.size)
                    for item in profile.evidence
                ),
            )
            for expected_path, expected_blob_sha, expected_size in expected_metadata:
                actual = self.evidence_fetcher.stat(source, expected_path)
                if actual.path != expected_path or actual.object_type != "file":
                    raise ParticipantEvidencePolicyError(
                        "catalog evidence must be an exact regular file"
                    )
                if actual.blob_sha != expected_blob_sha or actual.size != expected_size:
                    raise ParticipantEvidenceDriftError(
                        "catalog metadata differs from the reviewed snapshot"
                    )
                observed.append(actual)
        except ParticipantEvidenceError as error:
            reason_by_type = {
                ParticipantEvidenceMissingError: RiskIntakeReason.CATALOG_PATH_MISSING,
                ParticipantEvidenceDriftError: RiskIntakeReason.CATALOG_METADATA_DRIFT,
                ParticipantEvidencePolicyError: (
                    RiskIntakeReason.CATALOG_METADATA_POLICY_REJECTED
                ),
                ParticipantEvidenceAccessError: (
                    RiskIntakeReason.CATALOG_METADATA_UNAVAILABLE
                ),
            }
            reason = next(
                (code for kind, code in reason_by_type.items() if isinstance(error, kind)),
                RiskIntakeReason.CATALOG_METADATA_INVALID,
            )
            return self._unsupported(source, skill_id, reason)

        return KSkillIntakeResult(
            status=RiskIntakeStatus.PROFILED,
            source_ref=source.source_ref,
            skill_id=skill_id,
            profile=profile,
            source_snapshot=metadata_snapshot(source, tuple(observed)),
        )


def load_k_skill_catalog(
    path: Path | None = None,
) -> KSkillCatalogSnapshot:
    """Load and validate the packaged catalog; no network or upstream code access."""

    catalog_path = path or DEFAULT_K_SKILL_CATALOG_PATH
    raw = catalog_path.read_bytes()
    if len(raw) > MAX_K_SKILL_CATALOG_BYTES:
        raise CatalogSnapshotError("checked-in K-Skill catalog exceeds the byte budget")
    return KSkillCatalogSnapshot.model_validate_json(raw)


def metadata_fixture_for_catalog(
    catalog: KSkillCatalogSnapshot,
) -> dict[tuple[str, str], RepositoryEvidenceMetadata]:
    """Build an offline metadata fake from the checked-in path/blob/size facts."""

    source_ref = f"{catalog.source.repository}@{catalog.source.commit_sha}"
    catalog_manifest = catalog.source.catalog_manifest
    metadata = {
        (source_ref, catalog_manifest.path): RepositoryEvidenceMetadata(
            path=catalog_manifest.path,
            blob_sha=catalog_manifest.blob_sha,
            size=catalog_manifest.size,
            object_type="file",
        )
    }
    metadata.update({
        (source_ref, evidence.path): RepositoryEvidenceMetadata(
            path=evidence.path,
            blob_sha=evidence.blob_sha,
            size=evidence.size,
            object_type="file",
        )
        for profile in catalog.profiles
        for evidence in profile.evidence
    })
    return metadata


def k_skill_ids_from_manifest(content: bytes) -> tuple[str, ...]:
    """Parse canonical skill IDs from the bounded upstream catalog manifest."""

    if not content or len(content) > MAX_K_SKILL_MANIFEST_BYTES:
        raise CatalogSnapshotError("upstream K-Skill catalog manifest exceeds its byte budget")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogSnapshotError("upstream K-Skill catalog manifest is invalid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
        raise CatalogSnapshotError("upstream K-Skill catalog manifest has no skill list")

    names: list[str] = []
    for item in payload["skills"]:
        if not isinstance(item, str) or not item.startswith("./"):
            raise CatalogSnapshotError("upstream K-Skill catalog contains an invalid member")
        name = item[2:]
        if not _SKILL_ID_RE.fullmatch(name):
            raise CatalogSnapshotError("upstream K-Skill catalog contains an invalid member")
        names.append(name)
    if names != sorted(names) or len(names) != len(set(names)):
        raise CatalogSnapshotError("upstream K-Skill catalog members must be sorted and unique")
    return tuple(names)


def _commit_identity(commit_payload: Mapping[str, object]) -> tuple[str, str, str]:
    tree = commit_payload.get("tree")
    committer = commit_payload.get("committer")
    commit_sha = commit_payload.get("sha")
    if (
        not isinstance(tree, Mapping)
        or not isinstance(committer, Mapping)
        or not isinstance(commit_sha, str)
        or not isinstance(tree.get("sha"), str)
        or not isinstance(committer.get("date"), str)
    ):
        raise CatalogSnapshotError("GitHub commit metadata is malformed")
    try:
        return (
            _full_sha(commit_sha, field_name="commit sha"),
            _full_sha(tree["sha"], field_name="commit tree sha"),
            _timestamp(committer["date"]),
        )
    except ValueError as error:
        raise CatalogSnapshotError("GitHub commit metadata is malformed") from error


def _tree_entries(
    tree_payload: Mapping[str, object],
) -> tuple[str, dict[str, tuple[str, int]]]:
    if tree_payload.get("truncated") is not False:
        raise CatalogSnapshotError("GitHub recursive tree is missing or truncated")
    tree_sha = tree_payload.get("sha")
    raw_entries = tree_payload.get("tree")
    if not isinstance(tree_sha, str) or not isinstance(raw_entries, list):
        raise CatalogSnapshotError("GitHub tree metadata is malformed")
    try:
        normalized_tree = _full_sha(tree_sha, field_name="tree sha")
    except ValueError as error:
        raise CatalogSnapshotError("GitHub tree metadata is malformed") from error

    entries: dict[str, tuple[str, int]] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise CatalogSnapshotError("GitHub tree contains a malformed entry")
        path = raw_entry.get("path")
        mode = raw_entry.get("mode")
        object_type = raw_entry.get("type")
        blob_sha = raw_entry.get("sha")
        size = raw_entry.get("size")
        if (
            not isinstance(path, str)
            or mode not in {"100644", "100755"}
            or object_type != "blob"
            or not isinstance(blob_sha, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 1
        ):
            continue
        if path in entries:
            raise CatalogSnapshotError("GitHub tree contains duplicate paths")
        try:
            entries[path] = (_full_sha(blob_sha, field_name="blob sha"), size)
        except ValueError as error:
            raise CatalogSnapshotError("GitHub tree contains an invalid blob SHA") from error
    return normalized_tree, entries


def rebuild_k_skill_catalog_snapshot(
    catalog: KSkillCatalogSnapshot,
    tree_payload: Mapping[str, object],
    *,
    commit_payload: Mapping[str, object],
    upstream_catalog_manifest: bytes,
) -> KSkillCatalogSnapshot:
    """Verify and reproduce one already-reviewed snapshot without network access.

    The commit object binds the commit to the recursive tree. The upstream
    catalog bytes bind shortlist membership to the tree's catalog blob. A new
    source pin is deliberately rejected: it requires a new reviewed catalog
    version rather than carrying human classifications forward automatically.
    """

    commit_sha, commit_tree_sha, committed_at = _commit_identity(commit_payload)
    tree_sha, entries = _tree_entries(tree_payload)
    if commit_tree_sha != tree_sha:
        raise CatalogSnapshotError("GitHub commit does not identify the supplied tree")
    if (
        commit_sha != catalog.source.commit_sha
        or tree_sha != catalog.source.tree_sha
        or committed_at != catalog.source.committed_at
    ):
        raise CatalogSnapshotError(
            "source pin differs from the reviewed catalog; create a new reviewed version"
        )

    catalog_evidence = catalog.source.catalog_manifest
    observed_catalog = entries.get(catalog_evidence.path)
    if observed_catalog is None:
        raise CatalogSnapshotError("reviewed upstream catalog path is missing")
    if (
        observed_catalog != (catalog_evidence.blob_sha, catalog_evidence.size)
        or len(upstream_catalog_manifest) != catalog_evidence.size
        or git_blob_sha(upstream_catalog_manifest) != catalog_evidence.blob_sha
    ):
        raise CatalogSnapshotError("upstream catalog metadata differs from the reviewed snapshot")

    catalog_members = k_skill_ids_from_manifest(upstream_catalog_manifest)
    if len(catalog_members) != catalog_evidence.entry_count:
        raise CatalogSnapshotError(
            "upstream catalog entry count differs from the reviewed snapshot"
        )
    if not K_SKILL_SHORTLIST <= frozenset(catalog_members):
        raise CatalogSnapshotError("reviewed shortlist is missing from the upstream catalog")

    for profile in catalog.profiles:
        for evidence in profile.evidence:
            observed = entries.get(evidence.path)
            if observed is None:
                raise CatalogSnapshotError(
                    f"reviewed catalog path is missing: {evidence.path}"
                )
            if observed != (evidence.blob_sha, evidence.size):
                raise CatalogSnapshotError(
                    f"reviewed catalog metadata drifted: {evidence.path}"
                )

    return KSkillCatalogSnapshot.model_validate(catalog.model_dump(mode="json"))


__all__ = [
    "DEFAULT_K_SKILL_CATALOG_PATH",
    "K_SKILL_ADAPTER_VERSION",
    "K_SKILL_CATALOG_ENTRY_COUNT",
    "K_SKILL_CATALOG_MANIFEST_BLOB_SHA",
    "K_SKILL_CATALOG_MANIFEST_PATH",
    "K_SKILL_CATALOG_MANIFEST_SIZE",
    "K_SKILL_CATALOG_SCHEMA",
    "K_SKILL_CLAIM_BOUNDARY",
    "K_SKILL_PINNED_SHA",
    "K_SKILL_PINNED_TREE_SHA",
    "K_SKILL_REPOSITORY",
    "K_SKILL_SHORTLIST",
    "CatalogSnapshotError",
    "KSkillCatalogManifestEvidence",
    "KSkillCatalogSnapshot",
    "KSkillIntakeResult",
    "KSkillRiskProfileAdapter",
    "ProtectedAsset",
    "RequirementLevel",
    "RiskHypothesisFamily",
    "RiskIntakeReason",
    "RiskIntakeStatus",
    "SideEffectClass",
    "SkillEvidenceKind",
    "SkillRequirements",
    "SkillRiskProfile",
    "SkillRiskRegistry",
    "SkillSourceEvidence",
    "TrustBoundary",
    "k_skill_ids_from_manifest",
    "load_k_skill_catalog",
    "metadata_fixture_for_catalog",
    "rebuild_k_skill_catalog_snapshot",
]
