"""Explicit, source-pinned adapters for reviewed external Agent contracts.

The lab never imports or executes participant code.  An adapter is selected only
for one immutable source revision, inspects that revision with :mod:`ast`, and
then drives a network-disabled replacement facade whose trace keeps the
participant's tool names.  This is the narrow seam that turns a real external
tool contract into a reproducible Gym experiment without turning the host into a
Python package runner.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .fakes import FakeGym
from .models import (
    AntibodyPatch,
    LedgerEntry,
    RunResult,
    Scenario,
    TraceEvent,
    args_digest,
)
from .profile import AgentManifest
from .source import SourceDescriptor

UCP_REPOSITORY = "Upsonic/UCP-Agent"
UCP_COMMIT = "3f98ef03111e560afe92347333865ccac9081d93"
UCP_ENTRYPOINT = "upsonic_shopping_agent.py"
UCP_ADAPTER_ID = "ucp-shopping-v0"
UCP_ADAPTER_VERSION = "ucp-shopping-v0.1"

UCP_TOOL_NAMES: tuple[str, ...] = (
    "get_available_products",
    "get_available_discount_codes",
    "get_your_user",
    "discover_merchant",
    "create_cart",
    "apply_discount",
    "set_shipping_address",
    "complete_purchase",
)

UCP_IMPORTS: tuple[str, ...] = (
    "upsonic.Agent",
    "upsonic.Chat",
    "ucp_client.UCPAgentTools",
)

UCP_SCOPE = (
    "정확한 UCP source 커밋의 entrypoint를 AST로 정적으로 확인했으며, allowlisted "
    "ucp-shopping-v0 adapter가 네트워크 차단 local replacement tools로 실행됐다. "
    "upsonic/ucp_client dependency와 실제 merchant·결제 side effect는 실행하지 않았다."
)

LOCAL_ORDER_STATUS_TOOL = "get_order_status"
LOCAL_DELIVERY_TOOL = "adapter.local_delivery_confirmation"

_INTERNAL_TO_EXTERNAL = {
    "web.read": "get_available_products",
    "payment.charge": "complete_purchase",
    "payment.status": LOCAL_ORDER_STATUS_TOOL,
    "calendar.create": LOCAL_DELIVERY_TOOL,
}
_EXTERNAL_TO_INTERNAL = {value: key for key, value in _INTERNAL_TO_EXTERNAL.items()}
_EXTERNAL_TO_INTERNAL.update(
    {
        "get_available_products": "web.read",
        "complete_purchase": "payment.charge",
        LOCAL_ORDER_STATUS_TOOL: "payment.status",
        LOCAL_DELIVERY_TOOL: "calendar.create",
    }
)


class AdapterMatchError(ValueError):
    """The pinned source did not satisfy the reviewed adapter contract."""


class UCPShoppingAdapterContract(BaseModel):
    """Non-secret, non-source-content evidence for the selected adapter."""

    model_config = ConfigDict(frozen=True)

    adapter_id: Literal["ucp-shopping-v0"] = UCP_ADAPTER_ID
    adapter_version: str = UCP_ADAPTER_VERSION
    repository: str
    resolved_sha: str
    entrypoint: str
    source_blob_sha: str
    source_content_sha256: str
    observed_imports: tuple[str, ...]
    observed_tools: tuple[str, ...]
    system_prompt_sha256: str
    execution_mode: Literal["network_disabled_local_replacement"] = (
        "network_disabled_local_replacement"
    )
    network_access: Literal["disabled"] = "disabled"
    scope_note: str = UCP_SCOPE

    @property
    def source_ref(self) -> str:
        return f"{self.repository}@{self.resolved_sha}"

    def manifest(self, source: SourceDescriptor) -> AgentManifest:
        """Materialize a data-only manifest for the canonical planner/router."""

        return AgentManifest(
            name="Upsonic UCP Shopping Agent",
            source_ref=source.source_ref,
            system_prompt=f"[source prompt redacted; {self.system_prompt_sha256}]",
            tools=[
                # The source contract is intentionally represented with the
                # participant's names.  The adapter facade, not a name rewrite
                # in the report, is responsible for sandbox execution.
                _tool_spec(name) for name in self.observed_tools
            ],
            permissions={"max_spend_krw": 50_000},
            entrypoint=self.entrypoint,
            mission_family="purchase",
            manifest_hash=self.source_content_sha256.removeprefix("sha256:"),
            adapter_version=self.adapter_id,
            unsupported_tools=(),
        )


def _tool_spec(name: str):
    from .models import ToolSpec

    if name == "complete_purchase":
        return ToolSpec(
            name=name,
            description="Complete checkout through the UCP shopping contract.",
            side_effect=True,
            irreversible=True,
        )
    if name in {"create_cart", "apply_discount", "set_shipping_address"}:
        return ToolSpec(
            name=name,
            description="UCP shopping state mutation.",
            side_effect=True,
        )
    return ToolSpec(name=name, description="UCP shopping read or discovery operation.")


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324 - Git object identity


def _content_sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _imports(tree: ast.Module) -> set[str]:
    observed: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            observed.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            observed.update(alias.name for alias in node.names)
    return observed


def _system_prompt(tree: ast.Module) -> str:
    for node in tree.body:
        targets: Iterable[ast.expr]
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == "SYSTEM_PROMPT" for target in targets):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError) as error:
                raise AdapterMatchError("SYSTEM_PROMPT must be a literal string") from error
            if not isinstance(value, str) or not value.strip():
                raise AdapterMatchError("SYSTEM_PROMPT must be a non-empty literal string")
            return value
    raise AdapterMatchError("UCP entrypoint has no literal SYSTEM_PROMPT")


def _function_calls(function: ast.AST) -> set[str]:
    return {
        name
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        if (name := _dotted_name(node.func)) is not None
    }


def inspect_ucp_source(
    source: SourceDescriptor,
    *,
    path: str,
    content: bytes,
) -> UCPShoppingAdapterContract:
    """Validate the reviewed UCP source shape without importing or executing it."""

    if source.repository != UCP_REPOSITORY or source.resolved_sha != UCP_COMMIT:
        raise AdapterMatchError("source is not the pinned UCP adapter revision")
    normalized_path = path.replace("\\", "/").lstrip("/")
    if normalized_path != UCP_ENTRYPOINT or normalized_path != str(PurePosixPath(normalized_path)):
        raise AdapterMatchError("source path is not the reviewed UCP entrypoint")
    if not content or len(content) > 64_000:
        raise AdapterMatchError("UCP entrypoint is outside the bounded source limit")
    try:
        tree = ast.parse(content.decode("utf-8"), filename=normalized_path, mode="exec")
    except (UnicodeDecodeError, SyntaxError) as error:
        raise AdapterMatchError("UCP entrypoint is not valid UTF-8 Python") from error

    imports = _imports(tree)
    missing_imports = set(UCP_IMPORTS) - imports
    if missing_imports:
        raise AdapterMatchError(
            f"UCP entrypoint is missing reviewed imports: {sorted(missing_imports)}"
        )

    prompt = _system_prompt(tree)
    missing_tools = [name for name in UCP_TOOL_NAMES if name not in prompt]
    if missing_tools:
        raise AdapterMatchError(f"UCP SYSTEM_PROMPT is missing tool names: {missing_tools}")

    create_agent = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "create_agent"
        ),
        None,
    )
    if create_agent is None:
        raise AdapterMatchError("UCP entrypoint has no create_agent function")
    calls = _function_calls(create_agent)
    if "UCPAgentTools" not in calls or "Agent" not in calls or "agent.add_tools" not in calls:
        raise AdapterMatchError("create_agent does not construct and attach UCP tools")

    return UCPShoppingAdapterContract(
        repository=source.repository,
        resolved_sha=source.resolved_sha,
        entrypoint=normalized_path,
        source_blob_sha=_git_blob_sha(content),
        source_content_sha256=_content_sha256(content),
        observed_imports=tuple(sorted(UCP_IMPORTS)),
        observed_tools=UCP_TOOL_NAMES,
        system_prompt_sha256=_content_sha256(prompt.encode("utf-8")),
    )


def adapter_for_source(source: SourceDescriptor) -> bool:
    """Return whether the exact source is eligible for the UCP adapter."""

    return source.repository == UCP_REPOSITORY and source.resolved_sha == UCP_COMMIT


def _internal_tool(name: str) -> str:
    return _EXTERNAL_TO_INTERNAL.get(name, name)


def _internal_scenario(scenario: Scenario) -> Scenario:
    faults = tuple(
        fault.model_copy(update={"target_tool": _internal_tool(fault.target_tool)})
        for fault in scenario.faults
    )
    profile = "retry_happy" if scenario.aut_profile == "ucp_retry_happy" else scenario.aut_profile
    return scenario.model_copy(update={"faults": faults, "aut_profile": profile})


def _internal_patch(patch: AntibodyPatch | None) -> AntibodyPatch | None:
    if patch is None:
        return None
    return patch.model_copy(
        update={
            "side_effect_rules": [
                rule.model_copy(
                    update={
                        "tool": _internal_tool(rule.tool),
                        "reconcile_with": (
                            _internal_tool(rule.reconcile_with)
                            if rule.reconcile_with
                            else None
                        ),
                    }
                )
                for rule in patch.side_effect_rules
            ],
            "deny_rules": [
                deny.model_copy(
                    update={
                        "denied_tools": frozenset(
                            _internal_tool(tool) for tool in deny.denied_tools
                        )
                    }
                )
                for deny in patch.deny_rules
            ],
        }
    )


def _external_args(tool: str, args: dict) -> dict:
    if tool == "web.read":
        return {}
    if tool == "payment.charge":
        result = {"cart_id": "cart-001"}
        if isinstance(args.get("idempotency_key"), str):
            result["idempotency_key"] = args["idempotency_key"]
        return result
    if tool == "payment.status":
        result = {"cart_id": "cart-001"}
        if isinstance(args.get("idempotency_key"), str):
            result["idempotency_key"] = args["idempotency_key"]
        return result
    if tool == "calendar.create":
        return {"order_id": "ord-local"}
    return dict(args)


def _external_payload(payload):
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    if isinstance(result.get("reconcile_with"), str):
        result["reconcile_with"] = _INTERNAL_TO_EXTERNAL.get(
            result["reconcile_with"], result["reconcile_with"]
        )
    rule = result.get("rule")
    if isinstance(rule, dict) and isinstance(rule.get("denied_tools"), list):
        rule = dict(rule)
        rule["denied_tools"] = [
            _INTERNAL_TO_EXTERNAL.get(tool, tool) for tool in rule["denied_tools"]
        ]
        result["rule"] = rule
    return result


def _external_run(run: RunResult, *, scenario: Scenario, policy: AntibodyPatch | None) -> RunResult:
    trace: list[TraceEvent] = []
    for event in run.trace:
        if event.kind == "tool_call" and event.call is not None:
            internal_tool = event.call.tool
            external_tool = _INTERNAL_TO_EXTERNAL.get(internal_tool, internal_tool)
            trace.append(
                event.model_copy(
                    update={
                        "call": event.call.model_copy(
                            update={
                                "tool": external_tool,
                                "args": _external_args(internal_tool, event.call.args),
                            }
                        )
                    }
                )
            )
        elif event.kind == "tool_result" and event.result is not None:
            trace.append(
                event.model_copy(
                    update={
                        "result": event.result.model_copy(
                            update={"payload": _external_payload(event.result.payload)}
                        )
                    }
                )
            )
        else:
            trace.append(event)

    ledger: list[LedgerEntry] = []
    for entry in run.ledger:
        external_tool = _INTERNAL_TO_EXTERNAL.get(entry.tool, entry.tool)
        external_args = _external_args(
            entry.tool,
            {
                "idempotency_key": entry.idempotency_key,
            },
        )
        ledger.append(
            entry.model_copy(
                update={
                    "tool": external_tool,
                    "args_digest": (
                        entry.args_digest
                        if entry.tool not in {"payment.charge", "payment.status"}
                        else args_digest(external_args)
                    ),
                }
            )
        )
    return RunResult(
        scenario=scenario,
        policy_applied=policy,
        trace=trace,
        ledger=ledger,
        world_state=run.world_state,
        final_answer=run.final_answer,
        turns_used=run.turns_used,
    )


class UCPShoppingGym:
    """Run the UCP contract through a deterministic, network-disabled facade."""

    def __init__(self, contract: UCPShoppingAdapterContract) -> None:
        self.contract = contract
        self._gym = FakeGym()

    async def run(self, scenario: Scenario, policy: AntibodyPatch | None = None) -> RunResult:
        internal_scenario = _internal_scenario(scenario)
        internal_run = await self._gym.run(internal_scenario, _internal_patch(policy))
        return _external_run(internal_run, scenario=scenario, policy=policy)


__all__ = [
    "AdapterMatchError",
    "LOCAL_DELIVERY_TOOL",
    "LOCAL_ORDER_STATUS_TOOL",
    "UCP_ADAPTER_ID",
    "UCP_ADAPTER_VERSION",
    "UCP_COMMIT",
    "UCP_ENTRYPOINT",
    "UCP_IMPORTS",
    "UCP_REPOSITORY",
    "UCP_SCOPE",
    "UCPShoppingAdapterContract",
    "UCPShoppingGym",
    "UCP_TOOL_NAMES",
    "adapter_for_source",
    "inspect_ucp_source",
]
