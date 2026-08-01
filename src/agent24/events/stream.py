"""Ordered event capture shared by the SSE and JSONL transports.

The event bus deliberately has a very small contract.  Every event is assigned a
sequence number once, written to JSONL, and then fanned out to subscribers.  The
same dictionary is therefore used by the local log and by the SSE response.

For SDK tool events, ``payload`` is the JSON representation of the SDK's raw
tool-call/tool-result item.  We never put a display-oriented summary inside
that field; a summary, when useful, lives beside it in the envelope.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

TERMINAL_EVENT_TYPES = frozenset({"run_completed", "run_failed"})


class EventEnvelope(BaseModel):
    """Stable wire shape consumed by the frontend reducer.

    ``payload`` is intentionally untyped.  For ``tool_call`` and ``tool_result``
    it contains the raw SDK item without a presentation transform.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    seq: int = Field(ge=0)
    timestamp: str
    type: str
    payload: Any
    summary: str | None = None


def utc_timestamp() -> str:
    """Return a JSON-safe UTC timestamp with an explicit timezone."""

    return datetime.now(UTC).isoformat()


def to_jsonable(value: Any) -> Any:
    """Convert SDK/Python values to JSON while retaining raw object fields.

    Agents SDK response items are Pydantic models.  ``model_dump(mode="json")``
    is important here: it preserves the raw Responses API field names and also
    converts SDK-specific scalar values (for example enums) to JSON values.
    The recursive branches cover synthetic tool results and test doubles.
    """

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=False)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


class JsonlEventLog:
    """Append-only per-run JSONL storage for the raw event stream."""

    def __init__(self, root: str | Path = "artifacts/raw-streams") -> None:
        self.root = Path(root)

    def append(self, event: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{event['run_id']}.jsonl"
        # A single synchronous append keeps the ordering contract simple and is
        # bounded to one small event at a time.  No API key is ever included in
        # an envelope.
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
            stream.flush()

    def path_for(self, run_id: str) -> Path:
        return self.root / f"{run_id}.jsonl"


@dataclasses.dataclass
class RunChannel:
    """In-memory state for one run and its live subscribers."""

    run_id: str
    event_log: JsonlEventLog
    events: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    subscribers: set[asyncio.Queue[dict[str, Any] | None]] = dataclasses.field(
        default_factory=set
    )
    done: bool = False

    def publish(
        self, event_type: str, payload: Any, *, summary: str | None = None
    ) -> dict[str, Any]:
        """Create, persist, and fan out one ordered event."""

        envelope = EventEnvelope(
            run_id=self.run_id,
            seq=len(self.events),
            timestamp=utc_timestamp(),
            type=event_type,
            payload=to_jsonable(payload),
            summary=summary,
        )
        event = envelope.model_dump(mode="json", exclude_none=True)
        self.events.append(event)
        self.event_log.append(event)
        for subscriber in tuple(self.subscribers):
            subscriber.put_nowait(event)
        return event

    def close(self) -> None:
        """Mark the run complete and wake all currently connected SSE clients."""

        self.done = True
        for subscriber in tuple(self.subscribers):
            subscriber.put_nowait(None)


class EventManager:
    """Create channels and provide race-free replay/live subscriptions."""

    def __init__(self, event_log: JsonlEventLog | None = None) -> None:
        self.event_log = event_log or JsonlEventLog()
        self._channels: dict[str, RunChannel] = {}

    def create(self, run_id: str) -> RunChannel:
        if run_id in self._channels:
            raise ValueError(f"run already exists: {run_id}")
        channel = RunChannel(run_id=run_id, event_log=self.event_log)
        self._channels[run_id] = channel
        return channel

    def get(self, run_id: str) -> RunChannel | None:
        return self._channels.get(run_id)

    async def subscribe(
        self, run_id: str
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]]:
        """Return a backlog and a queue for events produced after subscription.

        There is intentionally no ``await`` before the queue is registered.  An
        event producer cannot interleave between copying the backlog and adding
        the subscriber on the same event loop.
        """

        channel = self._channels.get(run_id)
        if channel is None:
            raise KeyError(run_id)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        backlog = list(channel.events)
        if channel.done:
            queue.put_nowait(None)
        else:
            channel.subscribers.add(queue)
        return backlog, queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        channel = self._channels.get(run_id)
        if channel is not None:
            channel.subscribers.discard(queue)


async def iter_sse_events(
    manager: EventManager, run_id: str
) -> AsyncIterator[dict[str, Any]]:
    """Yield existing and newly published events in sequence order."""

    backlog, queue = await manager.subscribe(run_id)
    try:
        for event in backlog:
            yield event
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event
    finally:
        manager.unsubscribe(run_id, queue)


def encode_sse(event: Mapping[str, Any]) -> str:
    """Encode one event using the conventional SSE ``event``/``data`` fields."""

    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event['type']}\ndata: {data}\n\n"
