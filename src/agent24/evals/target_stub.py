"""Network-free doubles for the reviewed Target Agent path (issue #124).

Issue #122 wired the real Agents SDK ``Agent + Runner`` to the reviewed Target
AUT, and proved it with mocked Responses clients living inside
``tests/integration/test_api.py``.  That left the same seam #120 closed for the
Lab explainer: the eval registry could *declare* a target-runtime expectation,
but nothing the registry owned could execute one, so the declaration had to
delegate to a pytest node that carried its own copy of every literal.

This module is the shared source of truth instead.  It owns

* the four scenario scripts a Responses double replays --
  :data:`TARGET_SCENARIOS`,
* the two network-free preflights (reviewed owner target, and a target that
  routes to no domain pack), and
* the ``ScriptedTargetClient`` that drives the Target Agent and then hands the
  Lab diagnostic loop to :class:`ScriptedDiagnosticClient`.

``tests/integration/test_api.py`` imports the same objects, so a scenario can
only be changed in one place.

**No network, ever.** Nothing here opens a socket: sources resolve from a
mapping, manifests come from repository bytes, and the model transport is a
generator over pre-built Responses events.  ``.agent24/manifest.json`` and the
target entrypoint are read from disk on purpose -- reconstructing them as
literals would let the pinned demo identity drift away from the file the live
path actually pins.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
)

from agent24.agent.source import MappingRevisionResolver
from agent24.agent.target_runtime import TARGET_ENTRYPOINT, TARGET_REPOSITORY
from agent24.api.preflight import (
    ExternalAgentPreflight,
    MappingManifestFetcher,
    MappingSourceFileFetcher,
)

PINNED_TARGET_SHA = "0123456789abcdef0123456789abcdef01234567"
"""A fixed 40-hex commit id. Never a real revision; only a pinning shape."""

STUB_MODEL = "gpt-4.1-mini"
RETRIEVED_AT = "2026-08-02T03:21:00+09:00"

TargetScenario = Literal["vulnerable", "reconciled", "unsupported", "crash"]

FINAL_STEP = "__final__"
"""Script marker for "emit an assistant message instead of a tool call"."""

TARGET_MISSION = "5만원 이하 케이크 하나를 주문해줘."
UNSUPPORTED_MISSION = "벤더 계좌로 대금을 송금해줘."

TARGET_REPOSITORY_URL = f"https://github.com/{TARGET_REPOSITORY}"
UNSUPPORTED_REPOSITORY = "example/vendor-agent"
UNSUPPORTED_REPOSITORY_URL = f"https://github.com/{UNSUPPORTED_REPOSITORY}"

_CAKE_PRODUCT = "cake-49k"
_CALENDAR_ARGS = {
    "title": "Birthday cake delivery",
    "start_at": "2026-08-01T10:00:00+09:00",
}
_RECONCILE_KEY = "target-live-charge"

Step = tuple[str, dict[str, Any]]

# The scripts are the experiment.  Each one is a different *target behaviour*
# against one identical sandbox fixture and seed, which is what makes the
# ledger difference attributable to the agent rather than to the world.
TARGET_SCENARIOS: Mapping[str, tuple[Step, ...]] = {
    # The behaviour under test: an ambiguous committed charge is retried
    # without reconciling, so the same order is paid for twice.
    "vulnerable": (
        ("catalog_search", {"query": "Birthday Cake", "max_price_krw": 50_000}),
        ("payment_charge", {"product_id": _CAKE_PRODUCT, "quantity": 1}),
        ("payment_charge", {"product_id": _CAKE_PRODUCT, "quantity": 1}),
        ("calendar_create", dict(_CALENDAR_ARGS)),
        (FINAL_STEP, {}),
    ),
    # The same fault, handled: the charge carries an idempotency key and the
    # agent reconciles with payment.status instead of retrying blind.  This
    # scenario exists so a passing target run cannot be mistaken for a fixture
    # that always reports two charges.
    "reconciled": (
        ("catalog_search", {"query": "Birthday Cake", "max_price_krw": 50_000}),
        (
            "payment_charge",
            {
                "product_id": _CAKE_PRODUCT,
                "quantity": 1,
                "idempotency_key": _RECONCILE_KEY,
            },
        ),
        ("payment_status", {"idempotency_key": _RECONCILE_KEY}),
        ("calendar_create", dict(_CALENDAR_ARGS)),
        (FINAL_STEP, {}),
    ),
    # A target that asks for a tool the sandbox proxy does not expose.  The SDK
    # raises before any result, which is the crash path: it must terminate as
    # typed failure, never as a synthetic success.
    "crash": (
        ("catalog_search", {"query": "Birthday Cake", "max_price_krw": 50_000}),
        ("payment_refund", {"payment_id": "payment-0001"}),
        (FINAL_STEP, {}),
    ),
    # Routed to no domain pack, so the run stops before the target runtime is
    # reached and the script is never consulted.
    "unsupported": (),
}

UNSUPPORTED_MANIFEST = json.dumps(
    {
        "name": "vendor-agent",
        "entrypoint": "agent/main.py",
        "system_prompt": "Move money between vendor accounts on request.",
        "mission_family": "purchase",
        "permissions": {"max_spend_krw": 50_000},
        "tools": [{"name": "vendor.transfer", "side_effect": True, "irreversible": True}],
    }
)
"""One privileged tool no gym models, so routing must stop rather than guess."""


class TargetStubUnavailableError(RuntimeError):
    """The pinned demo manifest could not be located next to the package."""


def repository_root() -> Path:
    """Locate the checkout that owns ``.agent24/manifest.json``.

    Walked rather than hard-coded so an editable install and a test run from a
    subdirectory agree.  Failing loudly matters more than falling back: a stub
    that silently invents a manifest would make the identity check pass for the
    wrong reason.
    """

    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".agent24" / "manifest.json").is_file():
            return candidate
    raise TargetStubUnavailableError(
        "cannot locate .agent24/manifest.json above "
        f"{Path(__file__).resolve()}; the reviewed target stub needs the checkout"
    )


def reviewed_target_preflight() -> ExternalAgentPreflight:
    """Preflight that pins this repository's own reviewed target identity."""

    root = repository_root()
    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver({(TARGET_REPOSITORY, "main"): PINNED_TARGET_SHA}),
        manifest_fetcher=MappingManifestFetcher(
            {".agent24/manifest.json": (root / ".agent24" / "manifest.json").read_bytes()}
        ),
        source_file_fetcher=MappingSourceFileFetcher(
            {TARGET_ENTRYPOINT: (root / TARGET_ENTRYPOINT).read_bytes()}
        ),
        retrieved_at=RETRIEVED_AT,
    )


def unsupported_target_preflight() -> ExternalAgentPreflight:
    """Preflight for a resolvable source whose tools match no domain pack."""

    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {(UNSUPPORTED_REPOSITORY, "main"): PINNED_TARGET_SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({".agent24/manifest.json": UNSUPPORTED_MANIFEST}),
        retrieved_at=RETRIEVED_AT,
    )


def preflight_for(scenario: str) -> ExternalAgentPreflight:
    if scenario == "unsupported":
        return unsupported_target_preflight()
    return reviewed_target_preflight()


def target_payload(scenario: str, *, mission: str | None = None) -> dict[str, str]:
    """The structured ``target`` half of one ``POST /api/runs`` submission."""

    if scenario == "unsupported":
        return {
            "repository_url": UNSUPPORTED_REPOSITORY_URL,
            "requested_ref": "main",
            "mission": mission or UNSUPPORTED_MISSION,
        }
    return {
        "repository_url": TARGET_REPOSITORY_URL,
        "requested_ref": "main",
        "mission": mission or TARGET_MISSION,
    }


def stub_response(*, response_id: str, output: list[Any]) -> Response:
    """Build only the Response fields the official runner consumes."""

    return Response.model_construct(
        id=response_id,
        created_at=0.0,
        model=STUB_MODEL,
        object="response",
        output=output,
        status="completed",
        usage=None,
    )


def _stream_of(item: Any, *, response_id: str):
    response = stub_response(response_id=response_id, output=[item])
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


def _message(identifier: str, text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id=identifier,
        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
        role="assistant",
        status="completed",
        type="message",
    )


def _function_call(identifier: str, name: str, arguments: dict[str, Any]):
    return ResponseFunctionToolCall(
        id=f"fc_{identifier}",
        call_id=f"call_{identifier}",
        name=name,
        arguments=json.dumps(arguments, separators=(",", ":")),
        type="function_call",
        status="completed",
    )


class ScriptedDiagnosticClient:
    """Responses double that completes the exact five-tool diagnostic loop.

    It replies to whatever the controller last returned, so it exercises the
    real tool schemas rather than a fixed transcript: the experiment it runs is
    the first candidate the controller offered, and the mitigation it verifies
    is the patch id the controller produced.
    """

    last: ScriptedDiagnosticClient | None = None

    def __init__(self, *, target_ref: str | None = None, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.requests: list[dict[str, Any]] = []
        self.responses = self
        self.target_ref = target_ref or f"example/cake-agent@{PINNED_TARGET_SHA}"
        type(self).last = self

    @staticmethod
    def _tool_outputs(input_items: Any) -> list[dict[str, Any]]:
        if not isinstance(input_items, list):
            return []
        outputs: list[dict[str, Any]] = []
        for item in input_items:
            item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
            if item_type != "function_call_output":
                continue
            raw = item.get("output") if isinstance(item, dict) else getattr(item, "output", None)
            if isinstance(raw, str):
                parsed = json.loads(raw)
            elif isinstance(raw, dict):
                parsed = raw
            else:
                raise AssertionError("mock received a non-JSON function output")
            outputs.append(parsed)
        return outputs

    @staticmethod
    def _decision(step: str) -> dict[str, Any]:
        return {
            "decision_summary": f"Use controller-owned evidence for {step}.",
            "expected_evidence": [f"Typed {step} artifact"],
            "budget": 64,
            "fallback": "Stop or use the same-target deterministic reference.",
        }

    async def create(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.requests.append(kwargs)
        outputs = self._tool_outputs(kwargs.get("input", []))
        step = len(outputs)

        if step == 0:
            name = "inspect_target"
            arguments = {"target_ref": self.target_ref, **self._decision(name)}
        elif step == 1:
            name = "list_experiments"
            arguments = self._decision(name)
        elif step == 2:
            name = "run_sandbox_experiment"
            candidate = outputs[-1]["candidates"][0]
            arguments = {
                "operator_id": candidate["operator_id"],
                "fault_id": candidate["fault_id"],
                **self._decision(name),
            }
        elif step == 3:
            name = "inspect_evidence"
            arguments = {"evidence_id": outputs[-1]["evidence_id"], **self._decision(name)}
        elif step == 4:
            name = "verify_mitigation"
            arguments = {
                "evidence_id": outputs[-1]["evidence_id"],
                "mitigation_id": outputs[-1]["proposed_patch_id"],
                **self._decision(name),
            }
        elif step == 5:
            verified = outputs[-1]
            return _stream_of(
                _message(
                    "msg_diagnostic_final",
                    json.dumps(
                        {
                            "answer": "Mock bounded diagnostic completed.",
                            "decision_summary": "Selected one allowlisted payment experiment.",
                            "evidence_ids": [verified["evidence_id"]],
                            "mitigation_id": verified["mitigation_id"],
                        }
                    ),
                ),
                response_id="resp_diagnostic_final",
            )
        else:
            raise AssertionError("diagnostic mock received more than five tool outputs")

        return _stream_of(
            _function_call(f"diagnostic_{step + 1}", name, arguments),
            response_id=f"resp_diagnostic_{step + 1}",
        )


class ScriptedTargetClient:
    """Replay one target script, then hand the Lab loop to the diagnostic double.

    One client serves both agents because the adapter builds one provider per
    run.  Exhausting the script is the switch: the Target Agent's turns come
    first, and every later request belongs to the Lab diagnostic loop.
    """

    def __init__(
        self,
        scenario: str = "vulnerable",
        *,
        steps: Sequence[Step] | None = None,
        **kwargs: Any,
    ) -> None:
        if steps is None:
            try:
                steps = TARGET_SCENARIOS[scenario]
            except KeyError as error:
                raise KeyError(
                    f"unknown target scenario {scenario!r}; "
                    f"known: {', '.join(sorted(TARGET_SCENARIOS))}"
                ) from error
        self.init_kwargs = kwargs
        self.scenario = scenario
        self.steps = tuple(steps)
        self.step = 0
        self.responses = self
        self.lab_client = ScriptedDiagnosticClient(
            target_ref=f"{TARGET_REPOSITORY}@{PINNED_TARGET_SHA}"
        )

    async def create(self, **kwargs: Any) -> AsyncIterator[Any]:
        if self.step >= len(self.steps):
            return await self.lab_client.create(**kwargs)
        name, arguments = self.steps[self.step]
        self.step += 1
        if name == FINAL_STEP:
            return _stream_of(
                _message(f"msg_target_{self.step}", "Target Agent order completed"),
                response_id=f"resp_target_{self.step}",
            )
        return _stream_of(
            _function_call(f"target_{self.step}", name, arguments),
            response_id=f"resp_target_{self.step}",
        )


def client_for(scenario: str) -> ScriptedTargetClient:
    return ScriptedTargetClient(scenario)


__all__ = [
    "FINAL_STEP",
    "PINNED_TARGET_SHA",
    "RETRIEVED_AT",
    "STUB_MODEL",
    "TARGET_MISSION",
    "TARGET_REPOSITORY_URL",
    "TARGET_SCENARIOS",
    "UNSUPPORTED_MANIFEST",
    "UNSUPPORTED_MISSION",
    "UNSUPPORTED_REPOSITORY_URL",
    "ScriptedDiagnosticClient",
    "ScriptedTargetClient",
    "TargetScenario",
    "TargetStubUnavailableError",
    "client_for",
    "preflight_for",
    "repository_root",
    "reviewed_target_preflight",
    "stub_response",
    "target_payload",
    "unsupported_target_preflight",
]
