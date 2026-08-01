"""Read and validate the allowlisted, pinned AgentManifest file.

The loader is intentionally a *data* loader.  It opens one JSON file inside an
already pinned checkout, validates its schema, computes the manifest hash, and
returns the existing :class:`agent24.agent.profile.AgentManifest` contract.  It
does not import the entrypoint, run setup hooks, execute repository code, or
scan arbitrary files.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import MissionFamily, ToolSpec
from .profile import AgentManifest
from .source import SourceDescriptor

MANIFEST_VERSION = "agent24.manifest.v1"
"""Adapter version recorded in every loaded manifest."""

# Keep the P0 surface deliberately small and explicit.  The order is the
# discovery order; a caller can still pass an explicit path from this set.
ALLOWED_MANIFEST_PATHS: tuple[str, ...] = (
    ".agent24/manifest.json",
    "agent24.manifest.json",
)

# This is the tool vocabulary the local Life and read-only domain gyms can
# actually sandbox. A manifest may mention another tool, but it is returned as
# unsupported rather than being silently executed or treated as supported.
SUPPORTED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "catalog.search",
        "payment.charge",
        "payment.status",
        "payment_intent.create",
        "payment_intent.confirm",
        "payment_intent.retrieve",
        "order.create",
        "webhook.deliver",
        "order.fulfill",
        "web.fetch",
        "web.search",
        "email.send",
        "calendar.create",
        "file.write",
        "research.search",
        "paper.fetch",
        "table.read",
        "citation.resolve",
        "pdf.page.read",
        "repository.inspect",
        "dataset.inspect",
        "experiment.inspect",
        "ticker.resolve",
        "market.disclosures",
        "market.news",
        "market.record",
        "market.source",
        "entity.relations",
        "analyst_note.read",
        # Ticket surface (issue #57 routing seam).  Provisional: issue #60 owns
        # the real Ticket gym and may rename these.  They are listed so a ticket
        # manifest loads with a routable tool surface instead of every tool
        # landing in ``unsupported_tools``, which would make the pack
        # unreachable and the routing rule untestable.  Selecting the Ticket
        # pack still stops honestly until #60 lands its execution path.
        "ticket.search",
        "ticket.hold",
        "ticket.purchase",
        "ticket.cancel",
        "reservation.create",
        "reservation.cancel",
        # Concrete TicketWorld surface from issue #60.  Keep the provisional
        # aliases above for existing manifest/routing compatibility.
        "ticket.event.search",
        "ticket.inventory.read",
        "ticket.hold.create",
        "ticket.hold.retrieve",
        "ticket.purchase.confirm",
        "ticket.booking.retrieve",
        "ticket.hold.cancel",
        "ticket.booking.cancel",
        # Reviewed external adapter vocabulary.  These names are accepted as
        # data contracts; execution still requires the exact adapter match.
        "get_available_products",
        "get_available_discount_codes",
        "get_your_user",
        "discover_merchant",
        "create_cart",
        "apply_discount",
        "set_shipping_address",
        "complete_purchase",
    }
)


class ManifestLoadError(ValueError):
    """Base class for typed manifest errors shown to the caller."""


class ManifestNotFoundError(ManifestLoadError):
    """No allowlisted manifest exists in the pinned source root."""


class ManifestPathError(ManifestLoadError):
    """The requested manifest path is not an allowlisted safe path."""


class MalformedManifestError(ManifestLoadError):
    """Manifest bytes or schema are invalid."""


class PinnedSourceRequiredError(ManifestLoadError):
    """The loader was asked to read a source without immutable provenance."""


def _safe_manifest_path(source_root: Path, manifest_path: str) -> Path:
    normalized = manifest_path.replace("\\", "/").lstrip("/")
    if normalized not in ALLOWED_MANIFEST_PATHS:
        allowed = ", ".join(ALLOWED_MANIFEST_PATHS)
        raise ManifestPathError(f"manifest path is not allowlisted; choose one of: {allowed}")

    root = source_root.expanduser().resolve()
    unresolved = root / Path(normalized)
    if unresolved.is_symlink():
        raise ManifestPathError("manifest symlinks are not supported")
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ManifestPathError("manifest path escapes the pinned source root") from error
    # Resolve above and require the resolved file to remain below root.
    return candidate


def _discover_manifest(source_root: Path) -> tuple[Path, str]:
    for relative in ALLOWED_MANIFEST_PATHS:
        candidate = _safe_manifest_path(source_root, relative)
        if candidate.is_file():
            return candidate, relative
    allowed = ", ".join(ALLOWED_MANIFEST_PATHS)
    raise ManifestNotFoundError(f"no allowlisted manifest found; expected one of: {allowed}")


def _safe_entrypoint(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MalformedManifestError("entrypoint must be a non-empty relative path")
    path = value.replace("\\", "/")
    if path.startswith("/") or Path(path).drive or ".." in Path(path).parts:
        raise MalformedManifestError("entrypoint must stay inside the pinned source")
    if "\x00" in path or path.endswith("/"):
        raise MalformedManifestError("entrypoint is not a valid file path")
    return path


def _parse_tools(raw: Any) -> tuple[list[ToolSpec], tuple[str, ...]]:
    if raw is None:
        return [], ("missing:tools",)
    if not isinstance(raw, list):
        raise MalformedManifestError("tools must be a list of tool objects")

    tools: list[ToolSpec] = []
    unsupported: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise MalformedManifestError(f"tools[{index}] must be an object")
        try:
            spec = ToolSpec.model_validate(dict(item))
        except ValidationError as error:
            raise MalformedManifestError(f"tools[{index}] is invalid: {error}") from error
        if spec.name in seen:
            unsupported.append(f"duplicate:{spec.name}")
        seen.add(spec.name)
        if spec.name not in SUPPORTED_TOOL_NAMES:
            unsupported.append(f"unsupported:{spec.name}")
        tools.append(spec)
    return tools, tuple(sorted(set(unsupported)))


def _parse_manifest(
    raw: Any,
    *,
    source: SourceDescriptor,
    raw_bytes: bytes,
) -> AgentManifest:
    if not isinstance(raw, Mapping):
        raise MalformedManifestError("manifest root must be a JSON object")

    allowed_fields = {
        "name",
        "entrypoint",
        "system_prompt",
        "readme_text",
        "tools",
        "permissions",
        "mission_family",
        "adapter_version",
    }
    unknown_fields = sorted(set(raw) - allowed_fields)
    if unknown_fields:
        raise MalformedManifestError(
            f"manifest contains unsupported fields: {', '.join(unknown_fields)}"
        )
    if not isinstance(raw.get("name"), str) or not raw["name"].strip():
        raise MalformedManifestError("name must be a non-empty string")
    entrypoint = _safe_entrypoint(raw.get("entrypoint"))
    tools, unsupported = _parse_tools(raw.get("tools"))

    permissions = raw.get("permissions", {})
    if not isinstance(permissions, Mapping):
        raise MalformedManifestError("permissions must be a JSON object")
    mission_family = raw.get("mission_family", MissionFamily.UNKNOWN)
    try:
        mission_family = MissionFamily(mission_family)
    except (TypeError, ValueError) as error:
        raise MalformedManifestError(
            f"mission_family must be one of: {', '.join(item.value for item in MissionFamily)}"
        ) from error

    adapter_version = raw.get("adapter_version", MANIFEST_VERSION)
    if not isinstance(adapter_version, str) or not adapter_version.strip():
        raise MalformedManifestError("adapter_version must be a non-empty string")

    for field_name in ("system_prompt", "readme_text"):
        value = raw.get(field_name, "")
        if not isinstance(value, str):
            raise MalformedManifestError(f"{field_name} must be a string")

    return AgentManifest(
        name=raw["name"].strip(),
        source_ref=source.source_ref,
        readme_text=raw.get("readme_text", ""),
        system_prompt=raw.get("system_prompt", ""),
        tools=tools,
        permissions=dict(permissions),
        entrypoint=entrypoint,
        mission_family=mission_family,
        manifest_hash=hashlib.sha256(raw_bytes).hexdigest(),
        adapter_version=adapter_version,
        unsupported_tools=unsupported,
    )


def load_manifest(
    source_root: str | os.PathLike[str],
    source: SourceDescriptor,
    *,
    manifest_path: str | None = None,
) -> AgentManifest:
    """Load the allowlisted manifest from an immutable source checkout.

    ``source`` must already contain a full commit SHA.  The function reads only
    the selected JSON file; the declared entrypoint is metadata for later
    adapters and is never imported or executed here.
    """

    if not source.resolved_sha or len(source.resolved_sha) < 40:
        raise PinnedSourceRequiredError("manifest loading requires a full resolved commit SHA")
    root = Path(source_root)
    if not root.exists() or not root.is_dir():
        raise ManifestNotFoundError("pinned source root does not exist or is not a directory")
    if manifest_path is None:
        path, relative = _discover_manifest(root)
    else:
        relative = manifest_path.replace("\\", "/").lstrip("/")
        path = _safe_manifest_path(root, relative)
        if not path.is_file():
            raise ManifestNotFoundError(f"allowlisted manifest does not exist: {relative}")
    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MalformedManifestError(f"could not parse manifest {relative}: {error}") from error
    return _parse_manifest(raw, source=source, raw_bytes=raw_bytes)


load_agent_manifest = load_manifest


__all__ = [
    "ALLOWED_MANIFEST_PATHS",
    "MANIFEST_VERSION",
    "SUPPORTED_TOOL_NAMES",
    "ManifestLoadError",
    "ManifestNotFoundError",
    "ManifestPathError",
    "MalformedManifestError",
    "PinnedSourceRequiredError",
    "load_agent_manifest",
    "load_manifest",
]
