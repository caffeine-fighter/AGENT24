from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent24.agent.manifest import (
    MalformedManifestError,
    ManifestNotFoundError,
    ManifestPathError,
    load_manifest,
)
from agent24.agent.source import (
    InvalidSourceURLError,
    MappingRevisionResolver,
    SourceAccessError,
    SourceDescriptor,
    UnsupportedSourceHostError,
    parse_github_url,
    resolve_source,
)

SHA = "0123456789abcdef" * 2 + "0123456789abcdef"[:8]


def descriptor() -> SourceDescriptor:
    return SourceDescriptor(
        repository="caffeine-fighter/example-agent",
        repository_url="https://github.com/caffeine-fighter/example-agent",
        source_url="https://github.com/caffeine-fighter/example-agent/tree/main",
        requested_ref="main",
        resolved_sha=SHA,
        retrieved_at="2026-08-01T09:00:00+09:00",
        resolver="fixture",
    )


def test_github_url_parser_canonicalizes_repository_branch_tag_and_commit() -> None:
    root = parse_github_url("https://github.com/caffeine-fighter/example-agent.git", ref="main")
    branch = parse_github_url("https://github.com/caffeine-fighter/example-agent/tree/release/v1")
    tag = parse_github_url("https://github.com/caffeine-fighter/example-agent/releases/tag/v1.2.0")
    commit = parse_github_url(
        "https://github.com/caffeine-fighter/example-agent/commit/0123456abcdef"
    )

    assert root.repository == "caffeine-fighter/example-agent"
    assert root.requested_ref == "main"
    assert branch.requested_ref == "release/v1"
    assert branch.revision_kind == "branch_or_tag"
    assert tag.requested_ref == "v1.2.0"
    assert tag.revision_kind == "tag"
    assert commit.requested_ref == "0123456abcdef"
    assert commit.revision_kind == "commit"


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("https://gitlab.com/example/agent", "unsupported source host"),
        ("http://github.com/example/agent", "must use https"),
        ("https://github.com/example", "owner and repository"),
        ("https://github.com/example/agent/tree/main?x=1", "query"),
    ],
)
def test_source_parser_rejects_out_of_scope_inputs(url: str, match: str) -> None:
    error_type = UnsupportedSourceHostError if "gitlab" in url else InvalidSourceURLError
    with pytest.raises(error_type, match=match):
        parse_github_url(url)


def test_resolve_source_records_immutable_sha_and_is_byte_stable() -> None:
    resolver = MappingRevisionResolver(
        {("caffeine-fighter/example-agent", "main"): SHA}
    )
    first = resolve_source(
        "https://github.com/caffeine-fighter/example-agent",
        ref="main",
        resolver=resolver,
        retrieved_at="2026-08-01T09:00:00+09:00",
    )
    second = resolve_source(
        "https://github.com/caffeine-fighter/example-agent",
        ref="main",
        resolver=resolver,
        retrieved_at="2026-08-01T09:00:00+09:00",
    )

    assert first.source_ref == f"caffeine-fighter/example-agent@{SHA}"
    assert first.model_dump_json() == second.model_dump_json()
    assert first.canonical_json() == second.canonical_json()


def test_missing_revision_is_typed_access_error() -> None:
    resolver = MappingRevisionResolver({})
    with pytest.raises(SourceAccessError, match="no revision"):
        resolve_source(
            "https://github.com/caffeine-fighter/example-agent",
            ref="main",
            resolver=resolver,
            retrieved_at="2026-08-01T09:00:00+09:00",
        )


def test_manifest_loader_reads_only_allowlisted_json_and_never_runs_entrypoint(tmp_path) -> None:
    manifest_dir = tmp_path / ".agent24"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "cake-buyer",
                "entrypoint": "agent/main.py",
                "system_prompt": "Buy one cake under budget.",
                "readme_text": "Claims are not evidence.",
                "mission_family": "purchase",
                "permissions": {"max_spend_krw": 50000},
                "tools": [
                    {"name": "catalog.search"},
                    {"name": "payment.charge", "side_effect": True},
                    {"name": "vendor.transfer", "side_effect": True},
                    {"name": "vendor.transfer", "side_effect": True},
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_manifest(tmp_path, descriptor())

    assert loaded.source_ref == descriptor().source_ref
    assert loaded.entrypoint == "agent/main.py"
    assert loaded.manifest_hash
    assert loaded.adapter_version == "agent24.manifest.v1"
    assert loaded.unsupported_tools == ("duplicate:vendor.transfer", "unsupported:vendor.transfer")
    assert [tool.name for tool in loaded.tools] == [
        "catalog.search",
        "payment.charge",
        "vendor.transfer",
        "vendor.transfer",
    ]
    assert "agent/main.py" not in loaded.manifest_hash


def test_manifest_loader_rejects_malformed_or_unsafe_manifest(tmp_path) -> None:
    (tmp_path / "agent24.manifest.json").write_text(
        json.dumps({"name": "bad", "entrypoint": "../run.py", "tools": []}),
        encoding="utf-8",
    )
    with pytest.raises(MalformedManifestError, match="pinned source"):
        load_manifest(tmp_path, descriptor())

    with pytest.raises(ManifestPathError, match="allowlisted"):
        load_manifest(tmp_path, descriptor(), manifest_path="README.md")


def test_manifest_loader_reports_missing_manifest(tmp_path) -> None:
    with pytest.raises(ManifestNotFoundError, match="allowlisted manifest"):
        load_manifest(tmp_path, descriptor())


def test_repository_demo_manifest_is_a_supported_pinned_contract() -> None:
    repository_root = Path(__file__).resolve().parents[2]

    loaded = load_manifest(repository_root, descriptor())

    assert loaded.name == "nightmare-demo-cake-agent"
    assert loaded.mission_family == "purchase"
    assert loaded.unsupported_tools == ()
    assert {tool.name for tool in loaded.tools} >= {
        "catalog.search",
        "payment.charge",
        "payment.status",
    }
    assert loaded.permissions["max_spend_krw"] == 50_000
