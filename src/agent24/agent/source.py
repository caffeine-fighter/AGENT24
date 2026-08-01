"""Immutable GitHub source resolution for the Lab Agent input boundary.

The resolver is deliberately a metadata-only component.  It parses a GitHub
URL, resolves a branch/tag to a full commit SHA, and returns provenance that
can be attached to every later run.  It never clones, imports, or executes the
repository.  Tests inject :class:`MappingRevisionResolver`, while production
can use :class:`GitHubApiRevisionResolver` for public repositories.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, field_validator

GITHUB_HOST = "github.com"
GITHUB_API = "https://api.github.com"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REF_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")


class SourceResolutionError(ValueError):
    """Base class for safe, user-visible source resolution failures."""


class InvalidSourceURLError(SourceResolutionError):
    """The input is not a supported GitHub repository/revision URL."""


class UnsupportedSourceHostError(SourceResolutionError):
    """The URL points at a host outside the P0 support boundary."""


class SourceAccessError(SourceResolutionError):
    """A valid source could not be resolved or accessed."""


class SourceRequest(BaseModel):
    """Parsed, but not yet resolved, source input."""

    model_config = ConfigDict(frozen=True)

    repository: str
    repository_url: str
    source_url: str
    requested_ref: str | None = None
    revision_kind: str = "default"

    @field_validator("repository")
    @classmethod
    def _repository_shape(cls, value: str) -> str:
        parts = value.split("/")
        if len(parts) != 2 or any(not part for part in parts):
            raise ValueError("repository must use owner/name form")
        return value


class SourceDescriptor(BaseModel):
    """Canonical provenance for one immutable source revision."""

    model_config = ConfigDict(frozen=True)

    repository: str
    repository_url: str
    source_url: str
    requested_ref: str | None = None
    resolved_sha: str
    retrieved_at: str
    resolver: str = "github"

    @field_validator("repository")
    @classmethod
    def _repository_shape(cls, value: str) -> str:
        parts = value.split("/")
        if len(parts) != 2 or any(not part for part in parts):
            raise ValueError("repository must use owner/name form")
        return value

    @field_validator("resolved_sha")
    @classmethod
    def _full_sha(cls, value: str) -> str:
        normalized = value.lower()
        if not re.fullmatch(r"[0-9a-f]{40,64}", normalized):
            raise ValueError("resolved_sha must be a full hexadecimal commit SHA")
        return normalized

    @field_validator("retrieved_at")
    @classmethod
    def _timezone_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("retrieved_at must be an ISO-8601 timestamp") from error
        if parsed.tzinfo is None:
            raise ValueError("retrieved_at must include a timezone")
        return value

    @property
    def source_ref(self) -> str:
        """Stable source key used by the manifest and run provenance."""

        return f"{self.repository}@{self.resolved_sha}"

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class RevisionResolver(Protocol):
    """Resolve a parsed source request without touching repository contents."""

    name: str

    def resolve(self, request: SourceRequest) -> str: ...


def _clean_ref(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or not _REF_RE.fullmatch(value) or value.startswith("-"):
        raise InvalidSourceURLError("ref must be a non-empty Git ref without control characters")
    return value


def _canonical_repository(owner: str, repository: str) -> tuple[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        raise InvalidSourceURLError("GitHub owner is malformed")
    repository = repository.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repository):
        raise InvalidSourceURLError("GitHub repository name is malformed")
    return f"{owner}/{repository}", f"https://{GITHUB_HOST}/{owner}/{repository}"


def parse_github_url(url: str, *, ref: str | None = None) -> SourceRequest:
    """Parse repository, ``/tree/branch``, ``/commit/SHA`` or tag URLs.

    The parser accepts no query/fragment and only ``https://github.com``.  A
    branch/tag may be supplied either in the URL or through ``ref=``; providing
    both with different values is rejected instead of silently choosing one.
    """

    if not isinstance(url, str) or not url.strip():
        raise InvalidSourceURLError("GitHub URL is required")
    raw = url.strip()
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if host != GITHUB_HOST:
        raise UnsupportedSourceHostError(
            f"unsupported source host: {parsed.hostname or '<missing>'}; P0 supports github.com"
        )
    if parsed.scheme != "https":
        raise InvalidSourceURLError("GitHub source URL must use https")
    if parsed.query or parsed.fragment:
        raise InvalidSourceURLError("source URL must not contain query or fragment components")

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        raise InvalidSourceURLError("GitHub URL must include owner and repository")
    repository, repository_url = _canonical_repository(segments[0], segments[1])
    path_ref: str | None = None
    revision_kind = "default"
    if len(segments) == 2:
        pass
    elif segments[2] in {"tree", "commit"} and len(segments) >= 4:
        revision_kind = "commit" if segments[2] == "commit" else "branch_or_tag"
        path_ref = "/".join(segments[3:])
    elif (
        len(segments) >= 5
        and segments[2] == "releases"
        and segments[3] == "tag"
    ):
        revision_kind = "tag"
        path_ref = "/".join(segments[4:])
    else:
        raise InvalidSourceURLError(
            "supported GitHub URL paths are /owner/repo, /tree/<ref>, "
            "/commit/<sha>, and /releases/tag/<tag>"
        )

    explicit_ref = _clean_ref(ref)
    parsed_ref = _clean_ref(path_ref)
    if explicit_ref and parsed_ref and explicit_ref != parsed_ref:
        raise InvalidSourceURLError("URL ref and explicit ref do not match")
    requested_ref = explicit_ref or parsed_ref
    if revision_kind == "commit" and requested_ref and not _SHA_RE.fullmatch(requested_ref):
        raise InvalidSourceURLError("/commit/ URLs must contain a hexadecimal commit SHA")

    return SourceRequest(
        repository=repository,
        repository_url=repository_url,
        source_url=raw,
        requested_ref=requested_ref,
        revision_kind=revision_kind,
    )


def _validate_resolved_sha(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", normalized):
        raise SourceAccessError("resolver returned a non-immutable commit SHA")
    return normalized


class MappingRevisionResolver:
    """Network-free resolver fixture keyed by ``(repository, ref)``."""

    name = "fixture"

    def __init__(
        self,
        revisions: Mapping[tuple[str, str | None], str],
    ) -> None:
        self._revisions = dict(revisions)

    def resolve(self, request: SourceRequest) -> str:
        if request.revision_kind == "commit" and request.requested_ref:
            return _validate_resolved_sha(request.requested_ref)
        key = (request.repository, request.requested_ref)
        try:
            return _validate_resolved_sha(self._revisions[key])
        except KeyError as error:
            requested = request.requested_ref or "default"
            raise SourceAccessError(
                f"fixture has no revision for {request.repository}@{requested}"
            ) from error


class GitHubApiRevisionResolver:
    """Resolve public GitHub refs through the metadata-only REST API."""

    name = "github-api"

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        token: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.token = token

    def _get_json(self, path: str) -> dict[str, object]:
        request = Request(
            f"{GITHUB_API}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "agent24-nightmare-lab",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise SourceAccessError("GitHub source metadata could not be accessed") from error
        if not isinstance(payload, dict):
            raise SourceAccessError("GitHub source metadata returned an invalid response")
        return payload

    def resolve(self, request: SourceRequest) -> str:
        if request.revision_kind == "commit" and request.requested_ref:
            # A short commit URL is resolved through the API; a full SHA is safe
            # to retain without a request because it is already immutable.
            if len(request.requested_ref) == 40:
                return _validate_resolved_sha(request.requested_ref)
            path = f"/repos/{request.repository}/commits/{quote(request.requested_ref, safe='')}"
        else:
            revision = request.requested_ref
            if revision is None:
                repository = self._get_json(f"/repos/{request.repository}")
                revision = repository.get("default_branch")
                if not isinstance(revision, str) or not revision:
                    raise SourceAccessError("GitHub repository has no default branch metadata")
            path = f"/repos/{request.repository}/commits/{quote(revision, safe='')}"
        payload = self._get_json(path)
        sha = payload.get("sha")
        if not isinstance(sha, str):
            raise SourceAccessError("GitHub response did not contain a commit SHA")
        return _validate_resolved_sha(sha)


def resolve_source(
    url: str,
    *,
    ref: str | None = None,
    resolver: RevisionResolver | None = None,
    retrieved_at: str | None = None,
) -> SourceDescriptor:
    """Return immutable source provenance for a GitHub URL/ref pair."""

    request = parse_github_url(url, ref=ref)
    active_resolver = resolver or GitHubApiRevisionResolver()
    sha = _validate_resolved_sha(active_resolver.resolve(request))
    timestamp = retrieved_at or datetime.now(UTC).isoformat()
    try:
        return SourceDescriptor(
            repository=request.repository,
            repository_url=request.repository_url,
            source_url=request.source_url,
            requested_ref=request.requested_ref,
            resolved_sha=sha,
            retrieved_at=timestamp,
            resolver=getattr(active_resolver, "name", "custom"),
        )
    except ValueError as error:
        raise SourceAccessError(f"resolved source descriptor is invalid: {error}") from error


__all__ = [
    "GITHUB_HOST",
    "GitHubApiRevisionResolver",
    "InvalidSourceURLError",
    "MappingRevisionResolver",
    "RevisionResolver",
    "SourceAccessError",
    "SourceDescriptor",
    "SourceRequest",
    "SourceResolutionError",
    "UnsupportedSourceHostError",
    "parse_github_url",
    "resolve_source",
]
