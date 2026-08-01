"""Typed wire payloads for run-stage failures and the single terminal event."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ExecutionScope = Literal[
    "none",
    "compatibility_only",
    "synthetic_archetype",
    "allowlisted_adapter",
    "target_sandbox",
    "target_runtime",
    "no_target",
    "no_target_offline_demo",
]

StageName = Literal["source", "diagnostic", "openai_analysis", "target_runtime", "runtime"]


class StageFailurePayload(BaseModel):
    """Safe, non-terminal information about one failed pipeline stage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: StageName
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    pack_id: str | None = Field(default=None, max_length=120)


class RunTerminalPayload(BaseModel):
    """The canonical payload for the one final ``run_completed`` event.

    ``status`` and ``mode`` remain string fields for compatibility with older
    consumers.  The four stage fields are mandatory so a consumer never has to
    infer whether a completed-looking result came from the source, controller,
    or OpenAI explanation stage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str = Field(min_length=1, max_length=80)
    mode: str = Field(min_length=1, max_length=40)
    source_resolved: bool
    diagnostic_completed: bool
    openai_analysis_completed: bool
    execution_scope: ExecutionScope
    message: str | None = Field(default=None, max_length=500)
    experiments_run: int | None = Field(default=None, ge=0)
    findings: int | None = Field(default=None, ge=0)
    target_runtime_completed: bool | None = None
    target_charge_count: int | None = Field(default=None, ge=0)
    safety_boundary: Literal["SIMULATION_ONLY", "TARGET_CODE_IN_SANDBOX"] = "SIMULATION_ONLY"


__all__ = ["ExecutionScope", "RunTerminalPayload", "StageFailurePayload", "StageName"]
