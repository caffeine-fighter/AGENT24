"""A network-free Responses API double, shared by the registry and the tests.

Issue #107 left a seam: ``mode: live`` registry cases could not be executed by
the harness, because the only mocked OpenAI client lived inside
``tests/integration/test_api.py``. The harness returned an unconditional pass
and delegated to a bound pytest node, so the case's declared ``input``,
``expected_event_types`` and ``forbid_in_stream`` never reached anything that
ran. Editing the declaration changed nothing, which is the drift #120 is about.

Duplicating the stub would have created two fixtures to keep in sync -- the
reason the seam existed. Extracting it here gives both callers one source of
truth instead.

**No network, ever.** :func:`mocked_openai_provider` swaps
``AsyncOpenAI`` inside the official ``OpenAIProvider`` for :class:`StubOpenAIClient`
and restores it on exit. The production provider, runner and event path are all
still exercised; only the socket is gone.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
)

TEST_API_KEY = "test-only-key"
"""Never a real credential. Pinned so a stray ambient key cannot change a run."""

TEST_MODEL = "gpt-4.1-mini"
STUB_TOOL_NAME = "inspect_synthetic_gym"
STUB_FINAL_TEXT = "Mock final diagnosis"


def _response(*, response_id: str, output: list[Any]) -> Response:
    # ``model_construct`` lets the fixture stay focused on the fields consumed
    # by the official Agents SDK runner, without inventing provider metadata.
    return Response.model_construct(
        id=response_id,
        created_at=0.0,
        model=TEST_MODEL,
        object="response",
        output=output,
        status="completed",
        usage=None,
    )


def _has_tool_output(input_items: Any) -> bool:
    if not isinstance(input_items, list):
        return False
    return any(
        (item.get("type") if isinstance(item, dict) else getattr(item, "type", None))
        == "function_call_output"
        for item in input_items
    )


class StubOpenAIClient:
    """Small Responses API client double used through ``OpenAIProvider``.

    Two turns: one function call, then a final message once the tool output
    comes back. That is the shortest exchange that still produces a real
    ``tool_call`` / ``tool_result`` / ``final_output`` event sequence.
    """

    last: StubOpenAIClient | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.requests: list[dict[str, Any]] = []
        self.responses = self
        type(self).last = self

    async def create(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.requests.append(kwargs)
        if not _has_tool_output(kwargs.get("input", [])):
            item: Any = ResponseFunctionToolCall(
                id="fc_mock_1",
                call_id="call_mock_1",
                name=STUB_TOOL_NAME,
                arguments='{"query":"loop"}',
                type="function_call",
                status="completed",
            )
            response = _response(response_id="resp_mock_1", output=[item])
        else:
            item = ResponseOutputMessage(
                id="msg_mock_1",
                content=[
                    ResponseOutputText(
                        annotations=[], text=STUB_FINAL_TEXT, type="output_text"
                    )
                ],
                role="assistant",
                status="completed",
                type="message",
            )
            response = _response(response_id="resp_mock_2", output=[item])

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


@contextmanager
def mocked_openai_provider(
    *, api_key: str = TEST_API_KEY, model: str = TEST_MODEL
) -> Iterator[type[StubOpenAIClient]]:
    """Run the live path against :class:`StubOpenAIClient` instead of a socket.

    Only the ``AsyncOpenAI`` constructor inside the official provider is
    replaced, so ``OpenAIProvider``, ``Runner`` and the raw event forwarding are
    the production ones.

    ``OPENAI_API_KEY`` and ``OPENAI_MODEL`` are pinned for the duration and
    restored afterwards: the adapter reads the process environment first, and a
    real key in the ambient shell would otherwise decide what the run looks
    like. Both are put back exactly as they were, including being absent.
    """

    import agents.models.openai_provider as provider_module

    original_client = provider_module.AsyncOpenAI
    previous = {"OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
                "OPENAI_MODEL": os.environ.get("OPENAI_MODEL")}
    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_MODEL"] = model
    provider_module.AsyncOpenAI = StubOpenAIClient  # type: ignore[assignment]
    StubOpenAIClient.last = None
    try:
        yield StubOpenAIClient
    finally:
        provider_module.AsyncOpenAI = original_client  # type: ignore[assignment]
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


__all__ = [
    "STUB_FINAL_TEXT",
    "STUB_TOOL_NAME",
    "TEST_API_KEY",
    "TEST_MODEL",
    "StubOpenAIClient",
    "mocked_openai_provider",
]
