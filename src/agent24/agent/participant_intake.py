"""Pinned participant-repository compatibility intake.

This module turns an immutable :class:`SourceDescriptor` into a bounded target
profile without cloning, importing, installing, or executing repository code.
An owner-authored AgentManifest always wins.  When it is absent, the lab may
reuse an explicitly reviewed static profile only if every allowlisted evidence
path still has the expected Git blob SHA at the exact pinned commit.

The output is deliberately a *compatibility* claim.  It contains no observed
failure and cannot be used as a vulnerability or safety verdict.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal, Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import MissionFamily, canonical_json
from .packs import PACKS_BY_KIND, REGISTRY_VERSION, DomainKind
from .profile import AgentManifest
from .source import GITHUB_API, SourceDescriptor

PARTICIPANT_INTAKE_ADAPTER_VERSION = "participant-intake.v1"
MAX_STATIC_PROFILE_FILES = 4
MAX_STATIC_PROFILE_FILE_BYTES = 64_000
MAX_STATIC_PROFILE_TOTAL_BYTES = 128_000
MAX_GITHUB_METADATA_BYTES = 128_000

PARTICIPANT_CLAIM_BOUNDARY = (
    "Compatibility only: repository code was not executed, synthetic failures were not "
    "attributed to this target, and no vulnerability or safety certification was produced."
)

LJH_E2P_SHA = "dd8af1a00ff9229a3d589be5db500d983e4636df"
PAPER_PLAYGROUND_SHA = "bf6c545b6c3fd547819b76f9d3d96c5995d43eb0"
CREATIVE_STARTER_SHA = "f5527715e6ab6ef1f0ce7b2e098a5d181b5e26cb"

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_LINE_SELECTOR_RE = re.compile(r"^L([1-9][0-9]*)(?:-L([1-9][0-9]*))?$")
_ALLOWED_STATIC_SUFFIXES = frozenset(
    {".md", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".tsx", ".js", ".mjs"}
)
_SECRET_PATH_PARTS = frozenset(
    {
        ".env",
        "credentials",
        "credential",
        "secrets",
        "secret",
        "private_key",
        "id_rsa",
        "id_ed25519",
    }
)
TargetDomainKind = DomainKind
"""Compatibility alias for the canonical domain enum owned by ``packs.py``."""


_OWNER_DOMAIN_PREFIXES: tuple[tuple[TargetDomainKind, tuple[str, ...]], ...] = (
    (
        TargetDomainKind.RESEARCH,
        (
            "research.",
            "paper.",
            "citation.",
            "pdf.",
            "repository.",
            "dataset.",
            "experiment.",
        ),
    ),
    (
        TargetDomainKind.STOCK,
        ("ticker.", "market.", "entity.", "analyst_note."),
    ),
    (TargetDomainKind.TICKET, ("ticket.",)),
    (
        TargetDomainKind.LIFE,
        ("catalog.", "payment.", "payment_intent.", "order.", "email.", "calendar.", "file."),
    ),
)


class ProfileOrigin(StrEnum):
    OWNER_MANIFEST = "owner_manifest"
    LAB_STATIC_PROFILE = "lab_static_profile"


class CompatibilityStatus(StrEnum):
    COMPATIBLE_CANDIDATE = "compatible_candidate"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class StaticEvidenceKind(StrEnum):
    README = "readme"
    ARCHITECTURE = "architecture"
    TOOL_SCHEMA = "tool_schema"
    PROMPT_CONFIG = "prompt_config"
    OWNER_MANIFEST = "owner_manifest"


class ParticipantEvidenceError(ValueError):
    """Base class for sanitized static-profile evidence failures."""


class ParticipantEvidenceAccessError(ParticipantEvidenceError):
    """Pinned metadata could not be accessed."""


class ParticipantEvidenceMissingError(ParticipantEvidenceError):
    """An allowlisted path is absent at the pinned commit."""


class ParticipantEvidenceDriftError(ParticipantEvidenceError):
    """A reviewed path no longer matches its expected blob metadata."""


class ParticipantEvidencePolicyError(ParticipantEvidenceError):
    """A path or file violates the bounded intake policy."""


def _validated_sha(value: str, *, field_name: str) -> str:
    normalized = value.strip().lower()
    if not _FULL_SHA_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a full 40-character hexadecimal SHA")
    return normalized


def validate_static_evidence_path(value: str) -> str:
    """Accept only explicit, relative, non-secret text/config paths."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("evidence path must be a non-empty relative path")
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if normalized.startswith("/") or path.is_absolute() or ".." in path.parts:
        raise ValueError("evidence path must stay inside the pinned repository")
    if normalized != str(path) or any(ord(character) < 32 for character in normalized):
        raise ValueError("evidence path is not canonical")
    if len(path.parts) > 6:
        raise ValueError("evidence path exceeds the maximum directory depth")

    lowered_parts = tuple(part.lower() for part in path.parts)
    basename = lowered_parts[-1]
    if (
        any(part in _SECRET_PATH_PARTS for part in lowered_parts)
        or basename.startswith(".env")
        or basename.endswith((".pem", ".key", ".p12", ".pfx"))
        or any(token in basename for token in ("credential", "secret", "private_key"))
    ):
        raise ValueError("secret-like evidence paths are not allowed")
    if path.suffix.lower() not in _ALLOWED_STATIC_SUFFIXES:
        raise ValueError("evidence path must be a supported text, schema, or config file")
    return normalized


def _validate_line_selector(value: str) -> str:
    match = _LINE_SELECTOR_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError("line_selector must use Lx or Lx-Ly form")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if end < start:
        raise ValueError("line_selector end must not precede its start")
    return value.strip()


def _validate_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value


def git_blob_sha(content: bytes) -> str:
    """Return the Git blob object id for bytes without persisting the content."""

    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324 - Git object identity


class StaticEvidenceSpec(BaseModel):
    """One human-reviewed pointer in the checked-in static registry."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(min_length=1, max_length=80)
    kind: StaticEvidenceKind
    path: str
    expected_blob_sha: str
    expected_size: int = Field(ge=0)
    line_selector: str
    note: str = Field(min_length=1, max_length=300)

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        return validate_static_evidence_path(value)

    @field_validator("expected_blob_sha")
    @classmethod
    def _blob_sha(cls, value: str) -> str:
        return _validated_sha(value, field_name="expected_blob_sha")

    @field_validator("line_selector")
    @classmethod
    def _lines(cls, value: str) -> str:
        return _validate_line_selector(value)


class StaticDomainCandidateSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    domain_kind: TargetDomainKind
    mission_families: tuple[str, ...]
    tool_capabilities: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    rationale: str = Field(min_length=1, max_length=400)


class StaticTargetSpec(BaseModel):
    """A reviewed profile keyed by an immutable repository commit."""

    model_config = ConfigDict(frozen=True)

    repository: str
    resolved_sha: str
    agent_name: str
    reviewed_at: str
    public_visibility: Literal["public"] = "public"
    license_spdx: str | None = None
    evidence: tuple[StaticEvidenceSpec, ...]
    domain_candidates: tuple[StaticDomainCandidateSpec, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    unsupported_fields: tuple[str, ...] = ()

    @field_validator("resolved_sha")
    @classmethod
    def _source_sha(cls, value: str) -> str:
        return _validated_sha(value, field_name="resolved_sha")

    @field_validator("reviewed_at")
    @classmethod
    def _review_timestamp(cls, value: str) -> str:
        return _validate_timestamp(value)

    @model_validator(mode="after")
    def _candidate_evidence_exists(self) -> Self:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("static evidence_id values must be unique")
        known = set(evidence_ids)
        for candidate in self.domain_candidates:
            if not candidate.evidence_ids or not set(candidate.evidence_ids) <= known:
                raise ValueError("domain candidate must reference registered evidence")
        return self

    @property
    def source_ref(self) -> str:
        return f"{self.repository}@{self.resolved_sha}"


class RepositoryEvidenceMetadata(BaseModel):
    """Metadata-only response for one exact path at one exact commit."""

    model_config = ConfigDict(frozen=True)

    path: str
    blob_sha: str
    size: int = Field(ge=0)
    object_type: Literal["file", "symlink", "submodule", "directory"] = "file"

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        return validate_static_evidence_path(value)

    @field_validator("blob_sha")
    @classmethod
    def _blob_sha(cls, value: str) -> str:
        return _validated_sha(value, field_name="blob_sha")


def _content_sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _source_snapshot(
    source: SourceDescriptor,
    *,
    mode: Literal["bounded_download", "metadata_only"],
    execution_scope: Literal["manifest_only", "static_metadata_only", "none"],
    files: tuple[SourceSnapshotFile, ...] = (),
) -> SourceSnapshot:
    ordered_files = tuple(sorted(files, key=lambda item: item.path))
    payload: dict[str, object] = {
        "source_ref": source.source_ref,
        "mode": mode,
        "files": [item.model_dump(mode="json") for item in ordered_files],
        "total_bytes": sum(item.size for item in ordered_files),
        "execution_scope": execution_scope,
    }
    return SourceSnapshot(
        source_ref=source.source_ref,
        mode=mode,
        files=ordered_files,
        total_bytes=int(payload["total_bytes"]),
        execution_scope=execution_scope,
        snapshot_digest=f"sha256:{_sha256(payload)}",
    )


def bounded_manifest_snapshot(
    source: SourceDescriptor,
    *,
    manifest_path: str,
    manifest_bytes: bytes,
) -> SourceSnapshot:
    """Describe the only owner file fetched by preflight."""

    safe_path = validate_static_evidence_path(manifest_path)
    file = SourceSnapshotFile(
        path=safe_path,
        size=len(manifest_bytes),
        blob_sha=git_blob_sha(manifest_bytes),
        content_sha256=_content_sha256(manifest_bytes),
        retrieval_mode="bounded_download",
    )
    return _source_snapshot(
        source,
        mode="bounded_download",
        execution_scope="manifest_only",
        files=(file,),
    )


def metadata_snapshot(
    source: SourceDescriptor,
    metadata: tuple[RepositoryEvidenceMetadata, ...],
) -> SourceSnapshot:
    """Describe reviewed Git metadata without downloading file contents."""

    files = tuple(
        SourceSnapshotFile(
            path=item.path,
            size=item.size,
            blob_sha=item.blob_sha,
            retrieval_mode="metadata_only",
        )
        for item in metadata
    )
    return _source_snapshot(
        source,
        mode="metadata_only",
        execution_scope="static_metadata_only",
        files=files,
    )


class RepositoryEvidenceFetcher(Protocol):
    def stat(self, source: SourceDescriptor, path: str) -> RepositoryEvidenceMetadata: ...


class GitHubEvidenceMetadataFetcher:
    """Traverse Git tree metadata at a full SHA; never request blob contents."""

    def __init__(self, *, token: str | None = None, timeout_seconds: float = 5.0) -> None:
        self.token = token
        self.timeout_seconds = timeout_seconds
        self._tree_cache: dict[tuple[str, str], tuple[dict[str, object], ...]] = {}

    def _tree(
        self,
        source: SourceDescriptor,
        tree_sha: str,
    ) -> tuple[dict[str, object], ...]:
        cache_key = (source.repository, tree_sha)
        if cache_key in self._tree_cache:
            return self._tree_cache[cache_key]
        encoded_sha = quote(tree_sha, safe="")
        request = Request(
            f"{GITHUB_API}/repos/{source.repository}/git/trees/{encoded_sha}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "agent24-nightmare-lab",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read(MAX_GITHUB_METADATA_BYTES + 1)
        except HTTPError as error:
            if error.code == 404:
                raise ParticipantEvidenceMissingError(
                    "allowlisted evidence is absent at the pinned commit"
                ) from error
            raise ParticipantEvidenceAccessError(
                "pinned evidence metadata could not be accessed"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise ParticipantEvidenceAccessError(
                "pinned evidence metadata could not be accessed"
            ) from error

        if len(raw) > MAX_GITHUB_METADATA_BYTES:
            raise ParticipantEvidenceAccessError("GitHub evidence metadata response is oversized")
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, Mapping) or payload.get("truncated") is True:
                raise TypeError("tree metadata is invalid or truncated")
            raw_entries = payload["tree"]
            if not isinstance(raw_entries, list):
                raise TypeError("tree metadata does not contain entries")
            entries: list[dict[str, object]] = []
            for item in raw_entries:
                if not isinstance(item, Mapping):
                    raise TypeError("tree entry is not an object")
                path = item.get("path")
                mode = item.get("mode")
                object_type = item.get("type")
                sha = item.get("sha")
                size = item.get("size", 0)
                if not all(isinstance(value, str) for value in (path, mode, object_type, sha)):
                    raise TypeError("tree entry fields have invalid types")
                if not isinstance(size, int):
                    raise TypeError("tree entry size has an invalid type")
                entries.append(
                    {
                        "path": path,
                        "mode": mode,
                        "type": object_type,
                        "sha": _validated_sha(sha, field_name="tree entry sha"),
                        "size": size,
                    }
                )
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ParticipantEvidenceAccessError(
                "GitHub evidence metadata response is malformed"
            ) from error
        result = tuple(entries)
        self._tree_cache[cache_key] = result
        return result

    def stat(self, source: SourceDescriptor, path: str) -> RepositoryEvidenceMetadata:
        safe_path = validate_static_evidence_path(path)
        tree_sha = source.resolved_sha
        parts = PurePosixPath(safe_path).parts
        final: dict[str, object] | None = None
        for index, part in enumerate(parts):
            entry = next(
                (item for item in self._tree(source, tree_sha) if item["path"] == part),
                None,
            )
            if entry is None:
                raise ParticipantEvidenceMissingError(
                    "allowlisted evidence is absent at the pinned commit"
                )
            if index < len(parts) - 1:
                if entry["type"] != "tree":
                    raise ParticipantEvidencePolicyError(
                        "evidence path traverses a non-directory tree entry"
                    )
                tree_sha = str(entry["sha"])
                continue
            final = entry

        if final is None:
            raise ParticipantEvidenceMissingError(
                "allowlisted evidence is absent at the pinned commit"
            )
        mode = str(final["mode"])
        remote_type = str(final["type"])
        if mode == "120000":
            object_type = "symlink"
        elif mode == "160000" or remote_type == "commit":
            object_type = "submodule"
        elif remote_type == "tree":
            object_type = "directory"
        elif remote_type == "blob":
            object_type = "file"
        else:
            raise ParticipantEvidenceAccessError(
                "GitHub evidence metadata has an unsupported object type"
            )
        return RepositoryEvidenceMetadata(
            path=safe_path,
            blob_sha=str(final["sha"]),
            size=int(final["size"]),
            object_type=object_type,
        )


class MappingEvidenceMetadataFetcher:
    """Network-free metadata fixture keyed by ``(source_ref, path)``."""

    def __init__(
        self,
        metadata: Mapping[tuple[str, str], RepositoryEvidenceMetadata],
    ) -> None:
        self.metadata = dict(metadata)

    def stat(self, source: SourceDescriptor, path: str) -> RepositoryEvidenceMetadata:
        safe_path = validate_static_evidence_path(path)
        try:
            return self.metadata[(source.source_ref, safe_path)]
        except KeyError as error:
            raise ParticipantEvidenceMissingError(
                "fixture has no metadata for allowlisted evidence"
            ) from error


class TargetEvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    kind: StaticEvidenceKind
    repository: str
    resolved_sha: str
    path: str
    blob_sha: str
    line_selector: str
    note: str

    @field_validator("resolved_sha", "blob_sha")
    @classmethod
    def _sha(cls, value: str, info) -> str:
        return _validated_sha(value, field_name=info.field_name)

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        return validate_static_evidence_path(value)

    @field_validator("line_selector")
    @classmethod
    def _lines(cls, value: str) -> str:
        return _validate_line_selector(value)

    @property
    def stable_ref(self) -> str:
        return (
            f"github:{self.repository}@{self.resolved_sha}:"
            f"{self.path}@{self.blob_sha}#{self.line_selector}"
        )


class TargetProfileProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    origin: ProfileOrigin
    repository_url: str
    resolved_sha: str
    generated_at: str
    reviewed_at: str | None = None
    adapter_version: str = PARTICIPANT_INTAKE_ADAPTER_VERSION
    public_visibility: Literal["public", "private", "unknown"] = "unknown"
    license_spdx: str | None = None
    evidence: tuple[TargetEvidenceRef, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    unsupported_fields: tuple[str, ...] = ()

    @field_validator("resolved_sha")
    @classmethod
    def _source_sha(cls, value: str) -> str:
        return _validated_sha(value, field_name="resolved_sha")

    @field_validator("generated_at")
    @classmethod
    def _generation_timestamp(cls, value: str) -> str:
        return _validate_timestamp(value)

    @field_validator("reviewed_at")
    @classmethod
    def _review_timestamp(cls, value: str | None) -> str | None:
        return _validate_timestamp(value) if value is not None else None


class TargetDomainCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    domain_kind: TargetDomainKind
    mission_families: tuple[str, ...]
    tool_capabilities: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rationale: str


class SourceSnapshotFile(BaseModel):
    """One bounded file observation kept without retaining its content."""

    model_config = ConfigDict(frozen=True)

    path: str
    size: int = Field(ge=0)
    blob_sha: str
    content_sha256: str | None = None
    retrieval_mode: Literal["bounded_download", "metadata_only"]

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        return validate_static_evidence_path(value)

    @field_validator("blob_sha")
    @classmethod
    def _blob_sha(cls, value: str) -> str:
        return _validated_sha(value, field_name="blob_sha")

    @field_validator("content_sha256")
    @classmethod
    def _content_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value.strip().lower()):
            raise ValueError("content_sha256 must use sha256:<64 hex> form")
        return value.strip().lower()

    @model_validator(mode="after")
    def _content_hash_matches_mode(self) -> Self:
        if self.retrieval_mode == "bounded_download" and self.content_sha256 is None:
            raise ValueError("bounded_download snapshots require a content hash")
        if self.retrieval_mode == "metadata_only" and self.content_sha256 is not None:
            raise ValueError("metadata_only snapshots cannot contain content")
        return self


class SourceSnapshot(BaseModel):
    """Auditable source acquisition summary; never a source-code archive."""

    model_config = ConfigDict(frozen=True)

    source_ref: str
    mode: Literal["bounded_download", "metadata_only"]
    files: tuple[SourceSnapshotFile, ...] = ()
    total_bytes: int = Field(ge=0)
    execution_scope: Literal["manifest_only", "static_metadata_only", "none"]
    claim_boundary: str = PARTICIPANT_CLAIM_BOUNDARY
    snapshot_digest: str

    @field_validator("snapshot_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value.strip().lower()):
            raise ValueError("snapshot_digest must use sha256:<64 hex> form")
        return value.strip().lower()

    @model_validator(mode="after")
    def _size_matches_files(self) -> Self:
        if self.total_bytes != sum(item.size for item in self.files):
            raise ValueError("source snapshot total_bytes must match file sizes")
        if self.mode == "bounded_download" and any(
            item.retrieval_mode != "bounded_download" for item in self.files
        ):
            raise ValueError("bounded_download snapshots cannot contain metadata-only files")
        if self.mode == "metadata_only" and any(
            item.retrieval_mode != "metadata_only" for item in self.files
        ):
            raise ValueError("metadata_only snapshots cannot contain downloaded files")
        return self


class ParticipantTargetProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_name: str
    source_ref: str
    profile_label: Literal["OWNER MANIFEST", "LAB-INFERRED STATIC PROFILE"]
    status: CompatibilityStatus
    provenance: TargetProfileProvenance
    domain_candidates: tuple[TargetDomainCandidate, ...] = ()
    declared_capabilities: tuple[str, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    unsupported_fields: tuple[str, ...] = ()
    failure_claims: tuple[str, ...] = ()
    profile_digest: str

    @model_validator(mode="after")
    def _never_claim_target_failure(self) -> Self:
        if self.failure_claims:
            raise ValueError("static/manifest compatibility profiles cannot contain failure claims")
        return self


class TargetPackSelection(BaseModel):
    """Compatibility hand-off into the canonical DomainPack registry.

    This is deliberately not :class:`packs.PackSelection`: a static profile has
    no owner-declared manifest or BehaviorProfile to score.  It may name a
    registry candidate, but it cannot select a fixture or authorize execution.
    """

    model_config = ConfigDict(frozen=True)

    selection_id: str
    registry_version: str = REGISTRY_VERSION
    status: CompatibilityStatus
    selected_domain: TargetDomainKind | None = None
    candidate_domains: tuple[TargetDomainKind, ...] = ()
    pack_id: str | None = None
    pack_version: str | None = None
    fixture_id: str | None = None
    execution_mode: Literal["compatibility_only"] = "compatibility_only"
    why: str
    expect: str
    evidence_refs: tuple[str, ...] = ()
    max_experiments: Literal[0] = 0
    fallback: str = "terminal_compatibility_only"
    selection_digest: str


class TargetCompatibilityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: CompatibilityStatus
    source_ref: str
    profile_label: Literal["OWNER MANIFEST", "LAB-INFERRED STATIC PROFILE"]
    selected_domain: TargetDomainKind | None = None
    experiments_run: int = 0
    findings: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    compatibility_claims: tuple[str, ...] = ()
    claim_boundary: str = PARTICIPANT_CLAIM_BOUNDARY
    message: str
    report_digest: str

    @model_validator(mode="after")
    def _compatibility_is_not_a_finding(self) -> Self:
        if self.experiments_run != 0 or self.findings:
            raise ValueError("participant intake report cannot contain synthetic findings")
        return self


class ParticipantCompatibilityResult(BaseModel):
    """Terminal compatibility output for a source without an owner manifest."""

    model_config = ConfigDict(frozen=True)

    source: SourceDescriptor
    source_snapshot: SourceSnapshot
    target_profile: ParticipantTargetProfile
    pack_selection: TargetPackSelection
    compatibility_report: TargetCompatibilityReport

    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))


def _sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(dict(payload)).encode("utf-8")).hexdigest()


def _profile_status(candidates: tuple[TargetDomainCandidate, ...]) -> CompatibilityStatus:
    if len(candidates) == 1:
        return CompatibilityStatus.COMPATIBLE_CANDIDATE
    if len(candidates) > 1:
        return CompatibilityStatus.AMBIGUOUS
    return CompatibilityStatus.UNSUPPORTED


def _build_profile(
    *,
    agent_name: str,
    source: SourceDescriptor,
    profile_label: Literal["OWNER MANIFEST", "LAB-INFERRED STATIC PROFILE"],
    provenance: TargetProfileProvenance,
    candidates: tuple[TargetDomainCandidate, ...],
    declared_capabilities: tuple[str, ...],
    unknown_fields: tuple[str, ...],
    unsupported_fields: tuple[str, ...],
) -> ParticipantTargetProfile:
    status = _profile_status(candidates)
    digest_provenance = provenance.model_dump(mode="json")
    # Retrieval time is operational provenance, not part of compatibility
    # identity. The reviewed source/blob/adapter tuple keeps this digest stable.
    digest_provenance.pop("generated_at", None)
    payload: dict[str, object] = {
        "agent_name": agent_name,
        "source_ref": source.source_ref,
        "profile_label": profile_label,
        "status": status.value,
        "provenance": provenance.model_dump(mode="json"),
        "domain_candidates": [item.model_dump(mode="json") for item in candidates],
        "declared_capabilities": list(declared_capabilities),
        "unknown_fields": list(unknown_fields),
        "unsupported_fields": list(unsupported_fields),
        "failure_claims": [],
    }
    digest_payload = {**payload, "provenance": digest_provenance}
    return ParticipantTargetProfile(
        **payload,
        profile_digest=f"sha256:{_sha256(digest_payload)}",
    )


def _build_selection(profile: ParticipantTargetProfile) -> TargetPackSelection:
    candidate_domains = tuple(item.domain_kind for item in profile.domain_candidates)
    selected = candidate_domains[0] if len(candidate_domains) == 1 else None
    pack = PACKS_BY_KIND.get(selected) if selected is not None else None
    evidence_refs = tuple(
        sorted({ref for candidate in profile.domain_candidates for ref in candidate.evidence_refs})
    )
    if profile.status == CompatibilityStatus.COMPATIBLE_CANDIDATE:
        why = (
            f"Pinned {profile.profile_label.lower()} evidence supports the "
            f"{selected.value if selected else 'unknown'} DomainPack candidate"
            f" ({pack.pack_id if pack else 'unregistered'})."
        )
        expect = (
            "Compatibility intake stops here; the canonical owner-manifest router must "
            "independently authorize any synthetic experiment."
            if profile.profile_label == "OWNER MANIFEST"
            else (
                "Static compatibility evidence is terminal; an owner manifest is "
                "required before any synthetic experiment."
            )
        )
    elif profile.status == CompatibilityStatus.AMBIGUOUS:
        why = "More than one evidence-backed domain remains; no arbitrary tie-break was applied."
        expect = "Additional owner-provided capability evidence is required before routing."
    else:
        why = "Pinned evidence is insufficient for a supported DomainPack candidate."
        expect = "Terminate with zero experiments and no fabricated finding."

    payload: dict[str, object] = {
        "registry_version": REGISTRY_VERSION,
        "status": profile.status.value,
        "selected_domain": selected.value if selected else None,
        "candidate_domains": [item.value for item in candidate_domains],
        "pack_id": pack.pack_id if pack is not None else None,
        "pack_version": pack.version if pack is not None else None,
        "fixture_id": None,
        "execution_mode": "compatibility_only",
        "why": why,
        "expect": expect,
        "evidence_refs": list(evidence_refs),
        "max_experiments": 0,
        "fallback": "terminal_compatibility_only",
    }
    digest = _sha256(payload)
    return TargetPackSelection(
        selection_id=f"participant-selection-{digest[:16]}",
        selection_digest=f"sha256:{digest}",
        **payload,
    )


def _build_report(
    profile: ParticipantTargetProfile,
    selection: TargetPackSelection,
) -> TargetCompatibilityReport:
    if selection.status == CompatibilityStatus.COMPATIBLE_CANDIDATE:
        message = (
            f"{selection.selected_domain.value if selection.selected_domain else 'unknown'} pack "
            "compatibility candidate; no target code or synthetic attack was executed."
        )
        claims = (
            (f"Evidence supports a {selection.selected_domain.value} compatibility candidate.",)
            if selection.selected_domain
            else ()
        )
    elif selection.status == CompatibilityStatus.AMBIGUOUS:
        message = "Multiple domains remain plausible; routing stopped without an arbitrary attack."
        claims = ()
    else:
        message = "No supported domain has sufficient pinned evidence; zero experiments were run."
        claims = ()
    payload: dict[str, object] = {
        "status": selection.status.value,
        "source_ref": profile.source_ref,
        "profile_label": profile.profile_label,
        "selected_domain": selection.selected_domain.value if selection.selected_domain else None,
        "experiments_run": 0,
        "findings": [],
        "evidence_refs": list(selection.evidence_refs),
        "compatibility_claims": list(claims),
        "claim_boundary": PARTICIPANT_CLAIM_BOUNDARY,
        "message": message,
    }
    return TargetCompatibilityReport(**payload, report_digest=f"sha256:{_sha256(payload)}")


def _result(
    source: SourceDescriptor,
    profile: ParticipantTargetProfile,
    source_snapshot: SourceSnapshot,
) -> ParticipantCompatibilityResult:
    selection = _build_selection(profile)
    report = _build_report(profile, selection)
    return ParticipantCompatibilityResult(
        source=source,
        source_snapshot=source_snapshot,
        target_profile=profile,
        pack_selection=selection,
        compatibility_report=report,
    )


def _owner_candidates(
    manifest: AgentManifest,
    evidence_ref: str,
) -> tuple[TargetDomainCandidate, ...]:
    names = tuple(sorted({tool.name for tool in manifest.tools}))
    candidates: list[TargetDomainCandidate] = []
    for domain, prefixes in _OWNER_DOMAIN_PREFIXES:
        matching = tuple(name for name in names if name.startswith(prefixes))
        mission_match = (
            domain == TargetDomainKind.RESEARCH
            and manifest.mission_family == MissionFamily.RESEARCH
        )
        if not matching and not mission_match:
            continue
        mission_families = (
            (manifest.mission_family.value,)
            if manifest.mission_family != MissionFamily.UNKNOWN
            else ()
        )
        candidates.append(
            TargetDomainCandidate(
                domain_kind=domain,
                mission_families=mission_families,
                tool_capabilities=matching,
                evidence_refs=(evidence_ref,),
                rationale="Owner manifest explicitly declares the mission/tool surface.",
            )
        )
    return tuple(candidates)


def build_owner_manifest_compatibility(
    source: SourceDescriptor,
    manifest: AgentManifest,
    *,
    manifest_path: str,
    manifest_bytes: bytes,
) -> ParticipantCompatibilityResult:
    """Build owner-provenance compatibility; never inspect static registry data."""

    safe_path = validate_static_evidence_path(manifest_path)
    manifest_line_count = max(1, len(manifest_bytes.splitlines()))
    evidence = TargetEvidenceRef(
        evidence_id="owner-manifest",
        kind=StaticEvidenceKind.OWNER_MANIFEST,
        repository=source.repository,
        resolved_sha=source.resolved_sha,
        path=safe_path,
        blob_sha=git_blob_sha(manifest_bytes),
        line_selector=f"L1-L{manifest_line_count}",
        note=(
            "Owner-authored allowlisted manifest validated as data without executing "
            "its entrypoint."
        ),
    )
    candidates = _owner_candidates(manifest, evidence.stable_ref)
    unknown_fields = () if candidates else ("domain_kind", "supported_tool_capabilities")
    provenance = TargetProfileProvenance(
        origin=ProfileOrigin.OWNER_MANIFEST,
        repository_url=source.repository_url,
        resolved_sha=source.resolved_sha,
        generated_at=source.retrieved_at,
        public_visibility="unknown",
        evidence=(evidence,),
        unknown_fields=unknown_fields,
        unsupported_fields=manifest.unsupported_tools,
    )
    profile = _build_profile(
        agent_name=manifest.name,
        source=source,
        profile_label="OWNER MANIFEST",
        provenance=provenance,
        candidates=candidates,
        declared_capabilities=tuple(sorted({tool.name for tool in manifest.tools})),
        unknown_fields=unknown_fields,
        unsupported_fields=manifest.unsupported_tools,
    )
    return _result(
        source,
        profile,
        bounded_manifest_snapshot(
            source,
            manifest_path=safe_path,
            manifest_bytes=manifest_bytes,
        ),
    )


class StaticTargetRegistry:
    def __init__(self, specs: tuple[StaticTargetSpec, ...]) -> None:
        self._specs = {spec.source_ref: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ValueError("static target source_ref values must be unique")

    def get(self, source: SourceDescriptor) -> StaticTargetSpec | None:
        return self._specs.get(source.source_ref)

    def specs(self) -> tuple[StaticTargetSpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))


DEFAULT_PARTICIPANT_SPECS: tuple[StaticTargetSpec, ...] = (
    StaticTargetSpec(
        repository="ljh8450/Agent24",
        resolved_sha=LJH_E2P_SHA,
        agent_name="E2P Agent",
        reviewed_at="2026-08-01T20:30:00+09:00",
        license_spdx="MIT",
        evidence=(
            StaticEvidenceSpec(
                evidence_id="e2p-readme-workflow",
                kind=StaticEvidenceKind.README,
                path="README.md",
                expected_blob_sha="9aa8bfcdabd8fcc11fbb7c1b35431dd3498223f9",
                expected_size=3394,
                line_selector="L1-L14",
                note=(
                    "Declares public-statistics discovery and evidence-backed "
                    "persona/policy analysis."
                ),
            ),
            StaticEvidenceSpec(
                evidence_id="e2p-source-contract",
                kind=StaticEvidenceKind.TOOL_SCHEMA,
                path="app/contracts.py",
                expected_blob_sha="2bdc16ee7d6ca4d758b763acbdac32457245694b",
                expected_size=5402,
                line_selector="L118-L134",
                note=(
                    "Defines source provenance fields including snapshot hash and "
                    "observation period."
                ),
            ),
        ),
        domain_candidates=(
            StaticDomainCandidateSpec(
                domain_kind=TargetDomainKind.RESEARCH,
                mission_families=("research",),
                tool_capabilities=(
                    "public_statistics_search",
                    "source_provenance",
                    "compatibility_check",
                ),
                evidence_ids=("e2p-readme-workflow", "e2p-source-contract"),
                rationale=(
                    "Pinned documentation and source schema describe research-like "
                    "source discovery, "
                    "provenance, and compatibility checks."
                ),
            ),
        ),
        unknown_fields=("runtime_tool_schema", "observed_behavior", "failure_status"),
    ),
    StaticTargetSpec(
        repository="midwestchekhov/agent24",
        resolved_sha=PAPER_PLAYGROUND_SHA,
        agent_name="Paper Playground",
        reviewed_at="2026-08-01T20:30:00+09:00",
        evidence=(
            StaticEvidenceSpec(
                evidence_id="paper-readme-purpose",
                kind=StaticEvidenceKind.README,
                path="README.md",
                expected_blob_sha="cd883ecaa992d61a1cc2b8263ac63bd8915d10f3",
                expected_size=1330,
                line_selector="L1-L4",
                note=(
                    "Declares claim, evidence, assumption, and external-evidence "
                    "analysis for papers."
                ),
            ),
            StaticEvidenceSpec(
                evidence_id="paper-pipeline-stages",
                kind=StaticEvidenceKind.TOOL_SCHEMA,
                path="playground/pipeline.py",
                expected_blob_sha="b8aea2f5cdba81c39b591d353bfa2c150c940b75",
                expected_size=3205,
                line_selector="L32-L50",
                note=(
                    "Lists claim mapping, assumption mining, external verification, "
                    "critic, and render stages."
                ),
            ),
        ),
        domain_candidates=(
            StaticDomainCandidateSpec(
                domain_kind=TargetDomainKind.RESEARCH,
                mission_families=("research",),
                tool_capabilities=(
                    "paper_claim_mapping",
                    "external_evidence_verification",
                    "assumption_analysis",
                ),
                evidence_ids=("paper-readme-purpose", "paper-pipeline-stages"),
                rationale=(
                    "Pinned README and pipeline metadata describe a research evidence workflow."
                ),
            ),
        ),
        unknown_fields=("live_provider_behavior", "observed_behavior", "failure_status"),
        unsupported_fields=("license_spdx_unknown",),
    ),
    StaticTargetSpec(
        repository="youkim1028/agent24-creative-agent",
        resolved_sha=CREATIVE_STARTER_SHA,
        agent_name="Agent24 Creative Agent Starter",
        reviewed_at="2026-08-01T20:30:00+09:00",
        evidence=(
            StaticEvidenceSpec(
                evidence_id="starter-generic-domain",
                kind=StaticEvidenceKind.README,
                path="README.md",
                expected_blob_sha="152df45b917489fe6c4c6305224f27721c0006d0",
                expected_size=2944,
                line_selector="L50-L58",
                note=(
                    "Explicitly states that the creative domain is still generic "
                    "and must be replaced."
                ),
            ),
            StaticEvidenceSpec(
                evidence_id="starter-generic-pipeline",
                kind=StaticEvidenceKind.ARCHITECTURE,
                path="docs/architecture.md",
                expected_blob_sha="2da875bfe5d3d307919c01042c9d3c154b5b9689",
                expected_size=1940,
                line_selector="L21-L35",
                note=(
                    "Documents generic draft/review/save flow but no supported "
                    "domain-specific contract."
                ),
            ),
        ),
        unknown_fields=("domain_kind", "mission_family", "domain_tool_capabilities"),
        unsupported_fields=("generic_starter_requires_owner_manifest", "license_spdx_unknown"),
    ),
)

DEFAULT_STATIC_TARGET_REGISTRY = StaticTargetRegistry(DEFAULT_PARTICIPANT_SPECS)


class ParticipantStaticProfiler:
    """Verify and materialize a reviewed profile for an exact pinned source."""

    def __init__(
        self,
        *,
        registry: StaticTargetRegistry = DEFAULT_STATIC_TARGET_REGISTRY,
        evidence_fetcher: RepositoryEvidenceFetcher | None = None,
    ) -> None:
        self.registry = registry
        self.evidence_fetcher = evidence_fetcher or GitHubEvidenceMetadataFetcher()

    @staticmethod
    def _unknown_profile(
        source: SourceDescriptor,
        *,
        reason: str,
        source_snapshot: SourceSnapshot | None = None,
    ) -> ParticipantCompatibilityResult:
        unknown_fields = ("domain_kind", "mission_family", "tool_capabilities")
        unsupported_fields = (reason,)
        provenance = TargetProfileProvenance(
            origin=ProfileOrigin.LAB_STATIC_PROFILE,
            repository_url=source.repository_url,
            resolved_sha=source.resolved_sha,
            generated_at=source.retrieved_at,
            public_visibility="unknown",
            evidence=(),
            unknown_fields=unknown_fields,
            unsupported_fields=unsupported_fields,
        )
        profile = _build_profile(
            agent_name=source.repository.split("/", 1)[-1],
            source=source,
            profile_label="LAB-INFERRED STATIC PROFILE",
            provenance=provenance,
            candidates=(),
            declared_capabilities=(),
            unknown_fields=unknown_fields,
            unsupported_fields=unsupported_fields,
        )
        return _result(
            source,
            profile,
            source_snapshot
            or _source_snapshot(
                source,
                mode="metadata_only",
                execution_scope="none",
            ),
        )

    def profile(self, source: SourceDescriptor) -> ParticipantCompatibilityResult:
        spec = self.registry.get(source)
        if spec is None:
            return self._unknown_profile(source, reason="pinned_profile_not_registered")

        unique_paths = tuple(dict.fromkeys(item.path for item in spec.evidence))
        if len(unique_paths) > MAX_STATIC_PROFILE_FILES:
            return self._unknown_profile(source, reason="static_evidence_file_budget_exceeded")

        metadata_by_path: dict[str, RepositoryEvidenceMetadata] = {}
        try:
            total_bytes = 0
            for path in unique_paths:
                metadata = self.evidence_fetcher.stat(source, path)
                if metadata.path != path:
                    raise ParticipantEvidencePolicyError(
                        "evidence metadata path does not match the allowlisted path"
                    )
                if metadata.object_type != "file":
                    raise ParticipantEvidencePolicyError(
                        "symlink, submodule, and directory evidence is not allowed"
                    )
                if metadata.size > MAX_STATIC_PROFILE_FILE_BYTES:
                    raise ParticipantEvidencePolicyError(
                        "allowlisted evidence exceeds the per-file byte budget"
                    )
                total_bytes += metadata.size
                if total_bytes > MAX_STATIC_PROFILE_TOTAL_BYTES:
                    raise ParticipantEvidencePolicyError(
                        "allowlisted evidence exceeds the total byte budget"
                    )
                metadata_by_path[path] = metadata

            evidence_refs: list[TargetEvidenceRef] = []
            for expected in spec.evidence:
                actual = metadata_by_path[expected.path]
                if (
                    actual.blob_sha != expected.expected_blob_sha
                    or actual.size != expected.expected_size
                ):
                    raise ParticipantEvidenceDriftError(
                        "pinned evidence metadata no longer matches the reviewed profile"
                    )
                evidence_refs.append(
                    TargetEvidenceRef(
                        evidence_id=expected.evidence_id,
                        kind=expected.kind,
                        repository=source.repository,
                        resolved_sha=source.resolved_sha,
                        path=expected.path,
                        blob_sha=actual.blob_sha,
                        line_selector=expected.line_selector,
                        note=expected.note,
                    )
                )
        except ParticipantEvidenceError as error:
            reason_by_type = {
                ParticipantEvidenceMissingError: "static_evidence_missing",
                ParticipantEvidenceDriftError: "static_evidence_drift",
                ParticipantEvidencePolicyError: "static_evidence_policy_rejected",
                ParticipantEvidenceAccessError: "static_evidence_unavailable",
            }
            reason = next(
                (code for kind, code in reason_by_type.items() if isinstance(error, kind)),
                "static_evidence_invalid",
            )
            return self._unknown_profile(source, reason=reason)

        refs_by_id = {item.evidence_id: item.stable_ref for item in evidence_refs}
        candidates = tuple(
            TargetDomainCandidate(
                domain_kind=item.domain_kind,
                mission_families=item.mission_families,
                tool_capabilities=item.tool_capabilities,
                evidence_refs=tuple(refs_by_id[key] for key in item.evidence_ids),
                rationale=item.rationale,
            )
            for item in spec.domain_candidates
        )
        declared_capabilities = tuple(
            sorted({cap for candidate in candidates for cap in candidate.tool_capabilities})
        )
        provenance = TargetProfileProvenance(
            origin=ProfileOrigin.LAB_STATIC_PROFILE,
            repository_url=source.repository_url,
            resolved_sha=source.resolved_sha,
            generated_at=source.retrieved_at,
            reviewed_at=spec.reviewed_at,
            public_visibility=spec.public_visibility,
            license_spdx=spec.license_spdx,
            evidence=tuple(evidence_refs),
            unknown_fields=spec.unknown_fields,
            unsupported_fields=spec.unsupported_fields,
        )
        profile = _build_profile(
            agent_name=spec.agent_name,
            source=source,
            profile_label="LAB-INFERRED STATIC PROFILE",
            provenance=provenance,
            candidates=candidates,
            declared_capabilities=declared_capabilities,
            unknown_fields=spec.unknown_fields,
            unsupported_fields=spec.unsupported_fields,
        )
        metadata = tuple(metadata_by_path[path] for path in sorted(metadata_by_path))
        return _result(source, profile, metadata_snapshot(source, metadata))


def metadata_fixture_for(
    specs: tuple[StaticTargetSpec, ...] = DEFAULT_PARTICIPANT_SPECS,
) -> dict[tuple[str, str], RepositoryEvidenceMetadata]:
    """Return checked-in metadata only; useful for offline tests and demos."""

    return {
        (spec.source_ref, evidence.path): RepositoryEvidenceMetadata(
            path=evidence.path,
            blob_sha=evidence.expected_blob_sha,
            size=evidence.expected_size,
            object_type="file",
        )
        for spec in specs
        for evidence in spec.evidence
    }


__all__ = [
    "CREATIVE_STARTER_SHA",
    "DEFAULT_PARTICIPANT_SPECS",
    "DEFAULT_STATIC_TARGET_REGISTRY",
    "LJH_E2P_SHA",
    "MAX_STATIC_PROFILE_FILE_BYTES",
    "MAX_STATIC_PROFILE_FILES",
    "MAX_STATIC_PROFILE_TOTAL_BYTES",
    "PAPER_PLAYGROUND_SHA",
    "PARTICIPANT_CLAIM_BOUNDARY",
    "PARTICIPANT_INTAKE_ADAPTER_VERSION",
    "CompatibilityStatus",
    "GitHubEvidenceMetadataFetcher",
    "MappingEvidenceMetadataFetcher",
    "ParticipantCompatibilityResult",
    "ParticipantEvidenceAccessError",
    "ParticipantEvidenceDriftError",
    "ParticipantEvidenceError",
    "ParticipantEvidenceMissingError",
    "ParticipantEvidencePolicyError",
    "ParticipantStaticProfiler",
    "ParticipantTargetProfile",
    "ProfileOrigin",
    "RepositoryEvidenceFetcher",
    "RepositoryEvidenceMetadata",
    "SourceSnapshot",
    "SourceSnapshotFile",
    "StaticDomainCandidateSpec",
    "StaticEvidenceKind",
    "StaticEvidenceSpec",
    "StaticTargetRegistry",
    "StaticTargetSpec",
    "TargetCompatibilityReport",
    "TargetDomainCandidate",
    "TargetDomainKind",
    "TargetEvidenceRef",
    "TargetPackSelection",
    "TargetProfileProvenance",
    "build_owner_manifest_compatibility",
    "bounded_manifest_snapshot",
    "git_blob_sha",
    "metadata_snapshot",
    "metadata_fixture_for",
    "validate_static_evidence_path",
]
