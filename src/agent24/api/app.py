"""FastAPI transport for the one-input Nightmare Lab run workflow."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agent24.events import EventManager, JsonlEventLog, encode_sse, iter_sse_events

from .config import RuntimeSettings
from .preflight import ExternalTarget
from .runtime import OpenAIWhiteBoxAdapter


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=4_000)
    target: ExternalTarget | None = None


class RunAccepted(BaseModel):
    run_id: str
    status: str
    mode: str
    events_url: str


def create_app(
    *,
    runtime: OpenAIWhiteBoxAdapter | None = None,
    event_manager: EventManager | None = None,
    artifact_root: str | Path | None = None,
    web_root: str | Path | None = None,
) -> FastAPI:
    """Create an app; dependency injection keeps the integration test offline."""

    settings = RuntimeSettings()
    if event_manager is None:
        root = artifact_root or settings.raw_event_log_dir
        event_manager = EventManager(JsonlEventLog(root))
    runtime = runtime or OpenAIWhiteBoxAdapter(settings=settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            tasks = tuple(_app.state.tasks)
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="AGENT:24 Nightmare Lab", version="0.1.0", lifespan=lifespan)
    web_origin = settings.web_origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[web_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.state.event_manager = event_manager
    app.state.runtime = runtime
    app.state.tasks: set[asyncio.Task[Any]] = set()

    resolved_web_root = (
        Path(web_root).resolve()
        if web_root is not None
        else (Path(__file__).resolve().parents[3] / "web").resolve()
    )
    app.state.web_root = resolved_web_root

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Expose readiness without ever returning the API key."""

        return {
            "status": "ok",
            "mode": runtime.mode,
            "openai_configured": runtime.openai_configured,
            "github_configured": bool((runtime.settings.github_token or "").strip()),
            "sdk": "openai-agents",
        }

    @app.post(
        "/api/runs",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_run(request: RunRequest) -> RunAccepted:
        run_id = uuid.uuid4().hex
        channel = event_manager.create(run_id)
        task = asyncio.create_task(runtime.execute(request.input, channel, target=request.target))
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)
        return RunAccepted(
            run_id=run_id,
            status="queued",
            mode=runtime.mode,
            events_url=f"/api/runs/{run_id}/events",
        )

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str) -> StreamingResponse:
        if event_manager.get(run_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

        async def event_stream():
            async for event in iter_sse_events(event_manager, run_id):
                yield encode_sse(event)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Register the catch-all static mount last so API, health, and OpenAPI
    # routes always win. Starlette's StaticFiles also rejects traversal outside
    # the resolved directory.
    if resolved_web_root.is_dir() and (resolved_web_root / "index.html").is_file():
        app.mount(
            "/",
            StaticFiles(directory=str(resolved_web_root), html=True),
            name="nightmare-web",
        )
    else:

        @app.get("/", response_class=JSONResponse)
        async def web_unavailable() -> dict[str, str]:
            return {
                "service": "AGENT:24 Nightmare Lab API",
                "web_status": "missing",
                "hint": "Run from the repository checkout or pass create_app(web_root=...).",
            }

    return app


app = create_app()


__all__ = ["RunAccepted", "RunRequest", "app", "create_app"]
