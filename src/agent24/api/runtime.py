"""OpenAI Agents SDK runtime and deterministic offline fallback."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

from agents import Agent, OpenAIProvider, RunConfig, Runner
from agents.items import ToolCallItem, ToolCallOutputItem
from agents.stream_events import RunItemStreamEvent

from agent24.agent.manifest import ManifestLoadError
from agent24.agent.models import ExperimentPlan, StopDecision
from agent24.agent.prompts import LIVE_EXPLAINER_INSTRUCTIONS, LIVE_EXPLAINER_MAX_TURNS
from agent24.agent.source import GitHubApiRevisionResolver, SourceResolutionError
from agent24.events import RunChannel
from agent24.tools import SyntheticGym

from .config import RuntimeSettings
from .preflight import (
    ExternalAgentPreflight,
    ExternalTarget,
    GitHubContentsManifestFetcher,
    ManifestFetchError,
)

DEFAULT_MAX_TURNS = LIVE_EXPLAINER_MAX_TURNS
DEFAULT_TIMEOUT_SECONDS = 45.0


def _read_timeout(value: float | None) -> float:
    if value is not None:
        return max(0.1, value)
    raw_value = os.getenv("AGENT24_RUN_TIMEOUT_SECONDS", "")
    try:
        return max(0.1, float(raw_value)) if raw_value else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


class OpenAIWhiteBoxAdapter:
    """Run one input through an Agents SDK agent and the synthetic gym.

    The adapter intentionally does not implement an OpenAI request loop.  The
    official ``Runner`` owns model turns, tool dispatch, and the final answer;
    this class only observes semantic SDK stream events and forwards raw tool
    items to the event channel.

    ``openai_client`` and ``model_override`` are dependency-injection seams for
    an integration test.  Production reads ``OPENAI_API_KEY`` at runtime and
    passes it only to the official provider; it is never placed in an event or
    log.
    """

    def __init__(
        self,
        *,
        gym: SyntheticGym | None = None,
        runner: Any = Runner,
        openai_client: Any | None = None,
        model_override: Any | None = None,
        model_name: str | None = None,
        timeout_seconds: float | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        preflight: ExternalAgentPreflight | None = None,
        settings: RuntimeSettings | None = None,
    ) -> None:
        self.gym = gym or SyntheticGym()
        self.runner = runner
        self.openai_client = openai_client
        self.model_override = model_override
        self.settings = settings or RuntimeSettings()
        self.model_name = (
            model_name
            if model_name is not None
            else os.getenv("OPENAI_MODEL") or self.settings.openai_model or None
        )
        configured_timeout = (
            timeout_seconds if timeout_seconds is not None else self.settings.run_timeout_seconds
        )
        self.timeout_seconds = _read_timeout(configured_timeout)
        self.max_turns = max(1, max_turns)
        self.preflight = preflight or ExternalAgentPreflight(
            source_resolver=GitHubApiRevisionResolver(token=self.settings.github_token),
            manifest_fetcher=GitHubContentsManifestFetcher(token=self.settings.github_token),
        )

    def _api_key(self) -> str | None:
        # An explicitly exported process variable wins over .env settings.  No
        # caller receives this value; it is only handed to AsyncOpenAI.
        return os.getenv("OPENAI_API_KEY") or self.settings.openai_api_key

    @property
    def openai_configured(self) -> bool:
        """Whether the live SDK path is allowed to construct an OpenAI client."""

        return self.openai_client is not None or bool((self._api_key() or "").strip())

    @property
    def mode(self) -> str:
        return "live" if self.openai_configured else "offline_demo"

    def _agent(self) -> Agent:
        return Agent(
            name="Nightmare Lab AUT",
            # Versioned asset, not a literal: the live demo answer has to respect
            # the same evidence boundary as the offline report path, and a prompt
            # kept here would drift from the one the eval suite checks.
            instructions=LIVE_EXPLAINER_INSTRUCTIONS,
            tools=self.gym.tools(),
            model=self.model_override if self.model_override is not None else self.model_name,
        )

    def _run_config(self) -> RunConfig:
        if self.openai_client is not None:
            provider = OpenAIProvider(openai_client=self.openai_client)
        else:
            # The official provider lazily creates AsyncOpenAI and reads
            # OPENAI_API_KEY here.  Passing the secret through an event or a log
            # is deliberately avoided.
            provider = OpenAIProvider(api_key=self._api_key())
        return RunConfig(model_provider=provider, tracing_disabled=True)

    async def _run_live(self, query: str, channel: RunChannel) -> str:
        streamed = self.runner.run_streamed(
            self._agent(),
            query,
            max_turns=self.max_turns,
            run_config=self._run_config(),
        )
        async for stream_event in streamed.stream_events():
            if not isinstance(stream_event, RunItemStreamEvent):
                continue
            item = stream_event.item
            if stream_event.name == "tool_called" and isinstance(item, ToolCallItem):
                # raw_item is the SDK's unedited Responses API function-call
                # object.  The event layer performs JSON encoding only at the
                # transport boundary.
                channel.publish("tool_call", item.raw_item, summary=item.tool_name)
            elif stream_event.name == "tool_output" and isinstance(item, ToolCallOutputItem):
                channel.publish("tool_result", item.raw_item, summary=item.call_id)

        final_output = streamed.final_output
        return final_output if isinstance(final_output, str) else json.dumps(
            final_output, ensure_ascii=False, default=str
        )

    async def _run_offline(self, query: str, channel: RunChannel, *, reason: str) -> str:
        call_id = f"offline_{uuid.uuid4().hex}"
        arguments = json.dumps({"query": query[:240]}, ensure_ascii=False, separators=(",", ":"))
        channel.publish(
            "offline_demo",
            {"status": "offline_demo", "reason": reason},
            summary="SDK live path unavailable",
        )
        channel.publish(
            "tool_call",
            {
                "type": "function_call",
                "name": "inspect_synthetic_gym",
                "call_id": call_id,
                "arguments": arguments,
            },
            summary="inspect_synthetic_gym",
        )
        result = self.gym.offline_result(query)
        result_text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        channel.publish(
            "tool_result",
            {"type": "function_call_output", "call_id": call_id, "output": result_text},
            summary=call_id,
        )
        final_text = (
            f"Offline Nightmare Lab 진단: {result['label']} ({result['scenario_id']}). "
            f"관찰 신호는 '{result['failure_signal']}'이며, "
            f"우선 '{result['probe']}' 검증을 실행하세요."
        )
        channel.publish("final_output", {"text": final_text})
        return final_text

    async def _run_external_preflight(
        self,
        query: str,
        target: ExternalTarget,
        channel: RunChannel,
    ) -> bool:
        """Publish pinned planning artifacts, or finish with an honest fallback."""

        channel.publish("phase.changed", {"phase": "CLONE"}, summary="external preflight")
        try:
            result = await asyncio.to_thread(self.preflight.run, target)
        except (SourceResolutionError, ManifestLoadError, ManifestFetchError):
            channel.publish(
                "run_failed",
                {"status": "offline_demo", "code": "source_preflight_failed"},
                summary="External Agent source or manifest unavailable",
            )
            await self._run_offline(
                query,
                channel,
                reason="External Agent source or manifest unavailable",
            )
            channel.publish(
                "run_completed",
                {"status": "offline_demo", "mode": "offline_demo"},
            )
            return False

        channel.publish("source_descriptor", result.source, summary=result.source.source_ref)
        channel.publish("behavior_profile", result.profile, summary=result.profile.agent_name)
        if isinstance(result.decision, StopDecision):
            channel.publish(
                "run_completed",
                {
                    "status": "unsupported",
                    "mode": self.mode,
                    "message": result.decision.detail,
                },
                summary=result.decision.reason,
            )
            return False
        if not isinstance(result.decision, ExperimentPlan):
            raise TypeError("preflight returned an unknown decision type")
        channel.publish(
            "experiment_plan",
            result.decision,
            summary=result.decision.plan_id,
        )
        return True

    async def execute(
        self,
        query: str,
        channel: RunChannel,
        *,
        target: ExternalTarget | None = None,
    ) -> None:
        """Execute a run and always close the channel after its final event."""

        try:
            channel.publish(
                "run_started",
                {
                    "mode": self.mode,
                    "input_received": True,
                    **(
                        {
                            "target": {
                                "repositoryUrl": target.repository_url,
                                "requestedRef": target.requested_ref,
                                "resolvedSha": None,
                                "mission": target.mission,
                            },
                            "mission": target.mission,
                        }
                        if target is not None
                        else {}
                    ),
                },
            )
            if target is not None and not await self._run_external_preflight(
                query, target, channel
            ):
                return
            if not self.openai_configured:
                await self._run_offline(query, channel, reason="OPENAI_API_KEY is not configured")
                channel.publish("run_completed", {"status": "offline_demo", "mode": "offline_demo"})
                return

            try:
                async with asyncio.timeout(self.timeout_seconds):
                    final_text = await self._run_live(query, channel)
            except TimeoutError:
                channel.publish(
                    "run_failed",
                    {"status": "offline_demo", "code": "timeout"},
                    summary="OpenAI run timed out",
                )
                await self._run_offline(query, channel, reason="OpenAI run timed out")
                channel.publish("run_completed", {"status": "offline_demo", "mode": "offline_demo"})
                return
            except Exception as error:  # noqa: BLE001 - convert upstream failures to a demo state
                # Do not include the exception string: provider errors can echo
                # request metadata, while the event contract must never expose a
                # credential or transport detail.
                channel.publish(
                    "run_failed",
                    {"status": "offline_demo", "code": type(error).__name__},
                    summary="OpenAI run failed",
                )
                await self._run_offline(query, channel, reason="OpenAI run failed")
                channel.publish("run_completed", {"status": "offline_demo", "mode": "offline_demo"})
                return

            channel.publish("final_output", {"text": final_text})
            channel.publish("run_completed", {"status": "completed", "mode": "live"})
        finally:
            channel.close()


__all__ = ["DEFAULT_MAX_TURNS", "DEFAULT_TIMEOUT_SECONDS", "OpenAIWhiteBoxAdapter"]
