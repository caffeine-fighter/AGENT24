"""FastAPI routes and transport schemas."""

from .app import RunAccepted, RunRequest, app, create_app
from .config import RuntimeSettings
from .contracts import ExecutionScope, RunTerminalPayload, StageFailurePayload
from .preflight import (
    ExternalAgentPreflight,
    ExternalPreflightResult,
    ExternalTarget,
    GitHubContentsManifestFetcher,
    GitHubContentsSourceFetcher,
    ManifestFetchError,
    ManifestResponseError,
    ManifestUnavailableError,
    MappingManifestFetcher,
    MappingSourceFileFetcher,
    ParticipantCompatibilityResult,
    SourceFileFetchError,
)
from .runtime import OpenAIWhiteBoxAdapter

__all__ = [
    "OpenAIWhiteBoxAdapter",
    "ExternalAgentPreflight",
    "ExternalPreflightResult",
    "ExternalTarget",
    "GitHubContentsManifestFetcher",
    "GitHubContentsSourceFetcher",
    "ManifestFetchError",
    "ManifestResponseError",
    "ManifestUnavailableError",
    "MappingManifestFetcher",
    "MappingSourceFileFetcher",
    "ParticipantCompatibilityResult",
    "SourceFileFetchError",
    "RunAccepted",
    "RunRequest",
    "RuntimeSettings",
    "ExecutionScope",
    "RunTerminalPayload",
    "StageFailurePayload",
    "app",
    "create_app",
]
