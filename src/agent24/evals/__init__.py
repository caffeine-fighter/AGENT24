"""Deterministic evaluation helpers for demo and surprise-task rehearsal."""

from .surprise import (
    SURPRISE_CASES,
    SurpriseCase,
    SurpriseRunReport,
    evaluate_event_stream,
    parse_sse,
    run_http_case,
)

__all__ = [
    "SURPRISE_CASES",
    "SurpriseCase",
    "SurpriseRunReport",
    "evaluate_event_stream",
    "parse_sse",
    "run_http_case",
]
