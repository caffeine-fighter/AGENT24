from __future__ import annotations

import pytest

from agent24.agent.external_adapters import (
    UCP_COMMIT,
    UCP_ENTRYPOINT,
    UCP_REPOSITORY,
    UCP_SCOPE,
    AdapterMatchError,
    UCPShoppingGym,
    inspect_ucp_source,
)
from agent24.agent.loop import DeterministicLabLoop
from agent24.agent.models import ExperimentPlan
from agent24.agent.source import MappingRevisionResolver, SourceDescriptor
from agent24.api.preflight import (
    ExternalAgentPreflight,
    ExternalPreflightResult,
    ExternalTarget,
    MappingManifestFetcher,
    MappingSourceFileFetcher,
)

UCP_SOURCE = b'''
from upsonic import Agent, Chat
from ucp_client import UCPAgentTools

SYSTEM_PROMPT = """
Use get_available_products, get_available_discount_codes, get_your_user,
discover_merchant, create_cart, apply_discount, set_shipping_address, and
complete_purchase for the UCP shopping task.
"""

def create_agent(server_url):
    tools = UCPAgentTools(server_url)
    agent = Agent(name="shopping", system_prompt=SYSTEM_PROMPT)
    agent.add_tools(tools)
    return agent

if __name__ == "__main__":
    raise RuntimeError("the adapter must never execute this source")
'''


def ucp_source() -> SourceDescriptor:
    return SourceDescriptor(
        repository=UCP_REPOSITORY,
        repository_url=f"https://github.com/{UCP_REPOSITORY}",
        source_url=f"https://github.com/{UCP_REPOSITORY}/commit/{UCP_COMMIT}",
        requested_ref=UCP_COMMIT,
        resolved_sha=UCP_COMMIT,
        retrieved_at="2026-08-01T18:00:00+09:00",
        resolver="fixture",
    )


def test_ucp_source_is_ast_checked_without_executing_entrypoint() -> None:
    contract = inspect_ucp_source(
        ucp_source(),
        path=UCP_ENTRYPOINT,
        content=UCP_SOURCE,
    )

    assert contract.adapter_id == "ucp-shopping-v0"
    assert contract.entrypoint == UCP_ENTRYPOINT
    assert contract.observed_tools[-1] == "complete_purchase"
    assert contract.execution_mode == "network_disabled_local_replacement"
    assert contract.network_access == "disabled"
    assert contract.scope_note == UCP_SCOPE


@pytest.mark.parametrize(
    ("path", "resolved_sha"),
    [
        ("other.py", UCP_COMMIT),
        (UCP_ENTRYPOINT, "0123456789abcdef0123456789abcdef01234567"),
    ],
)
def test_ucp_adapter_requires_exact_reviewed_revision_and_path(
    path: str, resolved_sha: str
) -> None:
    source = ucp_source().model_copy(update={"resolved_sha": resolved_sha})
    with pytest.raises(AdapterMatchError):
        inspect_ucp_source(source, path=path, content=UCP_SOURCE)


def ucp_preflight() -> ExternalAgentPreflight:
    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {(UCP_REPOSITORY, UCP_COMMIT): UCP_COMMIT}
        ),
        # No allowlisted manifest: this forces the exact adapter path rather
        # than silently turning the source into a synthetic archetype.
        manifest_fetcher=MappingManifestFetcher({"README.md": "UCP shopping agent"}),
        source_file_fetcher=MappingSourceFileFetcher({UCP_ENTRYPOINT: UCP_SOURCE}),
        retrieved_at="2026-08-01T18:00:00+09:00",
    )


def test_ucp_preflight_builds_allowlisted_adapter_contract_and_plan() -> None:
    result = ucp_preflight().run(
        ExternalTarget(
            repository_url=f"https://github.com/{UCP_REPOSITORY}",
            requested_ref=UCP_COMMIT,
            mission="5만원 이하 상품 하나를 한 번만 구매해줘.",
        )
    )

    assert isinstance(result, ExternalPreflightResult)
    assert result.adapter_contract is not None
    assert result.source_snapshot.execution_scope == "allowlisted_adapter"
    assert result.target_profile.profile_label == "ALLOWLISTED ADAPTER"
    assert result.manifest.adapter_version == "ucp-shopping-v0"
    assert result.pack_selection.pack_id == "life-v0-sandbox.v1"
    assert isinstance(result.decision, ExperimentPlan)
    assert result.decision.scenario.faults[0].target_tool == "complete_purchase"


async def test_ucp_adapter_drives_network_disabled_gym_and_protected_replay() -> None:
    result = ucp_preflight().run(
        ExternalTarget(
            repository_url=f"https://github.com/{UCP_REPOSITORY}",
            requested_ref=UCP_COMMIT,
            mission="5만원 이하 상품 하나를 한 번만 구매해줘.",
        )
    )
    assert isinstance(result, ExternalPreflightResult)
    assert isinstance(result.decision, ExperimentPlan)
    assert result.adapter_contract is not None

    diagnostic = await DeterministicLabLoop().run(
        manifest=result.manifest,
        profile=result.profile,
        mission=result.mission,
        plan=result.decision,
        adapter_contract=result.adapter_contract,
    )
    assert diagnostic.execution_scope == UCP_SCOPE
    assert diagnostic.report.status.value == "verified_mitigation"
    assert diagnostic.verification is not None and diagnostic.verification.accepted
    assert [
        event.call.tool
        for event in diagnostic.perturbed.trace
        if event.kind == "tool_call" and event.call is not None
    ] == [
        "get_available_products",
        "complete_purchase",
        "complete_purchase",
        "adapter.local_delivery_confirmation",
    ]
    assert sum(entry.tool == "complete_purchase" for entry in diagnostic.perturbed.ledger) == 2
    assert sum(entry.tool == "complete_purchase" for entry in diagnostic.protected.ledger) == 1

    gym = UCPShoppingGym(result.adapter_contract)
    protected = await gym.run(result.decision.scenario, diagnostic.patch)
    assert all(entry.tool != "payment.charge" for entry in protected.ledger)
    assert sum(entry.tool == "complete_purchase" for entry in protected.ledger) == 1
