from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
)

from agent24.agent.manifest import load_manifest
from agent24.agent.sandbox_runner import (
    LOCAL_BUNDLE_ENTRYPOINT,
    LOCAL_BUNDLE_SHA256,
    LOCAL_BUNDLE_URI,
)
from agent24.agent.source import SourceDescriptor
from agent24.agent.target_runtime import (
    LOCAL_TARGET_AGENT_NAME,
    LOCAL_TARGET_REPOSITORY,
    TARGET_ADAPTER_HASH,
    TARGET_ENTRYPOINT,
    TARGET_PROMPT_HASH,
    TARGET_REPOSITORY,
    TARGET_RUNTIME_HASH,
    TARGET_RUNTIME_ID,
    build_local_target_instructions,
    build_target_agent,
    is_target_runtime_supported,
    run_protected_target_replay,
    run_target_agent,
    target_runtime_kind,
    target_runtime_provenance,
)
from agent24.tools import SandboxGym

PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"


def _source() -> SourceDescriptor:
    return SourceDescriptor(
        repository=TARGET_REPOSITORY,
        repository_url=f"https://github.com/{TARGET_REPOSITORY}",
        source_url=f"https://github.com/{TARGET_REPOSITORY}/commit/{PINNED_SHA}",
        requested_ref="main",
        resolved_sha=PINNED_SHA,
        retrieved_at="2026-08-02T03:21:00+09:00",
        resolver="fixture",
    )


def test_repository_manifest_selects_only_the_reviewed_target_runtime() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(root, _source())

    assert manifest.entrypoint == TARGET_ENTRYPOINT
    assert manifest.adapter_version == TARGET_RUNTIME_ID
    assert is_target_runtime_supported(_source(), manifest)

    other_source = _source().model_copy(update={"repository": "example/other-agent"})
    assert not is_target_runtime_supported(other_source, manifest)


def test_local_bundle_selects_reviewed_target_runtime_at_fixed_revision() -> None:
    root = Path(__file__).resolve().parents[2]
    source = SourceDescriptor(
        repository=LOCAL_TARGET_REPOSITORY,
        repository_url=LOCAL_BUNDLE_URI,
        source_url=LOCAL_BUNDLE_URI,
        requested_ref=LOCAL_BUNDLE_SHA256,
        resolved_sha=LOCAL_BUNDLE_SHA256,
        retrieved_at="2026-08-02T03:21:00+09:00",
        resolver="fixture",
        source_kind="local_bundle",
        source_path="examples/demo-agent-repo",
        revision_kind="bundle_sha256",
        bundle_sha256=LOCAL_BUNDLE_SHA256,
    )
    manifest = load_manifest(root / "examples/demo-agent-repo", source)

    assert manifest.entrypoint == LOCAL_BUNDLE_ENTRYPOINT
    assert target_runtime_kind(source, manifest) == "local"
    assert is_target_runtime_supported(source, manifest)
    assert target_runtime_provenance(
        "local", manifest=manifest, instructions=manifest.system_prompt
    )["prompt_hash"].startswith("sha256:")
    local_prompt = build_local_target_instructions(manifest.system_prompt)
    assert "max_price_krw=50000" in local_prompt
    assert manifest.system_prompt in local_prompt
    assert LOCAL_TARGET_AGENT_NAME != "Cake Buyer AUT"

    tampered = source.model_copy(
        update={"bundle_sha256": "0" * 64, "resolved_sha": "0" * 64}
    )
    assert not is_target_runtime_supported(tampered, manifest)


def test_target_agent_exposes_sandbox_tools_not_controller_tools() -> None:
    agent = build_target_agent(SandboxGym.from_fixture("life.cake_collision.v1"))

    assert agent.name == "Cake Buyer AUT"
    assert {tool.name for tool in agent.tools} == {
        "catalog_search",
        "payment_charge",
        "payment_status",
        "calendar_create",
    }
    assert all("oracle" not in tool.name for tool in agent.tools)


class _TargetResponsesClient:
    """Network-free Responses API stream that drives the real Agents Runner."""

    def __init__(self) -> None:
        self.responses = self
        self.step = 0
        self.calls = [
            ("catalog_search", {"query": "Birthday Cake", "max_price_krw": 50_000}),
            ("payment_charge", {"product_id": "cake-49k", "quantity": 1}),
            ("payment_charge", {"product_id": "cake-49k", "quantity": 1}),
            (
                "calendar_create",
                {"title": "Birthday Cake delivery", "start_at": "2026-08-01T10:00:00+09:00"},
            ),
        ]

    async def create(self, **_kwargs: Any) -> AsyncIterator[Any]:
        if self.step < len(self.calls):
            name, arguments = self.calls[self.step]
            self.step += 1
            item = ResponseFunctionToolCall(
                id=f"fc_target_{self.step}",
                call_id=f"call_target_{self.step}",
                name=name,
                arguments=json.dumps(arguments),
                type="function_call",
                status="completed",
            )
        else:
            item = ResponseOutputMessage(
                id="msg_target_final",
                content=[ResponseOutputText(annotations=[], text="주문 완료", type="output_text")],
                role="assistant",
                status="completed",
                type="message",
            )
        response = Response.model_construct(
            id=f"resp_target_{self.step}",
            created_at=0.0,
            model="test-target-agent",
            object="response",
            output=[item],
            status="completed",
            usage=None,
        )
        events = [
            ResponseOutputItemDoneEvent(
                item=item,
                output_index=0,
                sequence_number=1,
                type="response.output_item.done",
            ),
            ResponseCompletedEvent(
                response=response,
                sequence_number=2,
                type="response.completed",
            ),
        ]

        async def stream() -> AsyncIterator[Any]:
            for event in events:
                yield event

        return stream()


def test_real_runner_drives_the_vulnerable_target_and_records_ledger() -> None:
    async def run() -> None:
        sandbox = SandboxGym.from_fixture(
            "life.cake_collision.v1",
            seed=42,
            run_id="target-test",
            fault_enabled=True,
        )
        seen: list[str] = []

        result = await run_target_agent(
            sandbox,
            mission="예산 안에서 생일 케이크 하나를 주문해줘.",
            openai_client=_TargetResponsesClient(),
            on_event=lambda kind, _item, _summary: seen.append(kind),
        )

        assert result.tool_calls == result.tool_results == 4
        assert result.charge_count == 2
        assert result.spend_krw == 98_000
        assert seen == ["tool_call", "tool_result"] * 4
        assert [entry["tool"] for entry in result.evidence["ledger"]] == [
            "payment.charge",
            "payment.charge",
            "calendar.create",
        ]

    asyncio.run(run())


def test_protected_target_replay_reconciles_same_fixture_to_one_charge() -> None:
    replay = run_protected_target_replay(seed=42)

    assert replay.accepted is True
    assert replay.charge_count == 1
    assert replay.spend_krw == 49_000
    assert replay.evidence["reconciled"] is True
    assert replay.evidence["first_charge_status"] == "unknown"
    assert replay.evidence["payment_status"]["status"] == "committed"


def test_target_evidence_hashes_are_stable_sha256_values() -> None:
    for value in (TARGET_ADAPTER_HASH, TARGET_PROMPT_HASH, TARGET_RUNTIME_HASH):
        assert value.startswith("sha256:")
        assert len(value) == len("sha256:") + 64
