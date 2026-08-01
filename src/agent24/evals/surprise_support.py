"""D1 support classification for the documented Surprise missions.

``surprise.py`` judges the legacy one-field ``{"input": ...}`` path, where
:class:`~agent24.tools.gym.SyntheticGym` routes a scenario by keyword and every
documented mission therefore "passes".  The real ``web/`` form does not use that
path: it submits a structured ``target``, which goes through the external
preflight, the domain-pack router, and the deterministic lab loop.

On that path a mission's *text* does not select anything.  ``preflight.py``
builds ``Mission(text=target.mission, family=manifest.mission_family, ...)``, so
routing reads the submitted repository's manifest and not the sentence a judge
typed.  A Surprise mission about a calendar loop, submitted against a payment
manifest, therefore produces a payment finding.  That substitution is silent --
nothing in the event stream says the requested domain was never tested.

This module is the eval-side answer to that, and it does three things:

* :func:`classify_support` decides, from the pack registry rather than from a
  hand-written verdict list, whether D1 can honestly stage a given Surprise
  domain against a given tool surface.
* :func:`evaluate_support_run` gates one complete event stream against that
  classification, and reports :attr:`SurpriseSupportReport.silent_substitution`
  when an unsupported mission came back carrying payment evidence.
* :func:`terminal_digest` reduces a stream to the fields that a rerun of the
  same fixture and seed must reproduce byte for byte.

It deliberately does not change the router.  Which pack runs is issue #57's
decision and D1's payment-only scope is a product decision; an eval that quietly
rewrote either would be measuring itself.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent24.agent.mission_support import (
    DOMAIN_REQUIREMENTS,
    MISSIONS_BY_ID,
    SURPRISE_MISSIONS,
    UNSUPPORTED_REASON,
    DomainRequirement,
    MissionSupport,
    SupportVerdict,
    SurpriseDomain,
    SurpriseMission,
    classify_matrix,
    classify_support,
    executable_packs,
)
from agent24.agent.models import canonical_json

# --------------------------------------------------------------------------
# Payment-substitution evidence
# --------------------------------------------------------------------------

PAYMENT_TOOLS: frozenset[str] = frozenset(
    {
        "order.create",
        "order.fulfill",
        "payment.charge",
        "payment.status",
        "payment_intent.confirm",
        "payment_intent.create",
        "payment_intent.retrieve",
        "webhook.deliver",
    }
)
"""Tool names whose appearance in a trace means a payment experiment ran."""

PAYMENT_FIXTURE_PREFIXES: tuple[str, ...] = (
    "life.cake_collision",
    "life.payment_intent_timeout",
    "life.webhook_",
    "sc-duplicate",
)
"""Fixture and scenario ids that identify the cake/payment analysis."""


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for nested in value.values() for text in _strings(nested)]
    if isinstance(value, list | tuple):
        return [text for nested in value for text in _strings(nested)]
    return []


def payment_evidence(events: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Cite every place the stream proves a payment/cake experiment ran.

    Returns pointers of the form ``"events[7].gym.tool_call:payment.charge"``
    rather than a bare boolean, because "this run was silently substituted" is a
    claim that has to survive being checked by hand.
    """

    citations: list[str] = []
    for index, event in enumerate(events):
        kind = str(event.get("type", ""))
        payload = _payload(event)

        if kind == "protected_replay":
            citations.append(f"events[{index}].protected_replay")
            continue

        tool = payload.get("tool")
        if kind in {"gym.tool_call", "gym.tool_result"} and tool in PAYMENT_TOOLS:
            citations.append(f"events[{index}].{kind}:{tool}")
            continue

        # A router naming a payment-capable pack or an unsupported report
        # repeating the submitted manifest's tools does not prove that a
        # payment experiment ran.  Only a planned/executed finding does.
        unsupported_report = (
            kind == "finding_report" and payload.get("status") == "unsupported"
        ) or (
            kind == "lab_report"
            and isinstance(payload.get("termination"), Mapping)
            and payload["termination"].get("reason") == UNSUPPORTED_REASON
        )
        if kind in {"experiment_plan", "finding_report", "lab_report"} and not unsupported_report:
            for text in _strings(payload):
                if text in PAYMENT_TOOLS:
                    citations.append(f"events[{index}].{kind}:{text}")
                    break
                if text.startswith(PAYMENT_FIXTURE_PREFIXES):
                    citations.append(f"events[{index}].{kind}:{text}")
                    break

    return tuple(citations)


# --------------------------------------------------------------------------
# Terminal digest
# --------------------------------------------------------------------------

_DIGEST_FIELDS: tuple[str, ...] = (
    "status",
    "reason",
    "code",
    "phase",
    "pack_id",
    "plan_id",
    "scenario_id",
    "seed",
    "selection_digest",
    "tool",
    "run_digest",
)
"""Payload keys a rerun of the same fixture and seed must reproduce.

``run_id``, timestamps, and the offline path's ``uuid4`` call ids are excluded
on purpose: they are expected to differ between two identical runs, so folding
them in would make every digest unique and the determinism gate vacuous.

``selection_digest`` and ``plan_id`` are included because without them two runs
that took *different* routes can share a digest: the ``pack.selected`` payload
nests its decision under ``selected``, so a top-level projection would reduce
both to a bare ``{seq, type}``.  Both are deterministic by construction --
``packs.selection_digest`` hashes the selection and the planner is a pure
function -- so they discriminate routes without making reruns differ.
"""


def digest_projection(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reduce a stream to its deterministic skeleton."""

    projection: list[dict[str, Any]] = []
    for event in events:
        payload = _payload(event)
        entry: dict[str, Any] = {
            "seq": event.get("seq"),
            "type": event.get("type"),
        }
        for field in _DIGEST_FIELDS:
            if field in payload:
                entry[field] = payload[field]
        projection.append(entry)
    return projection


def terminal_digest(events: Sequence[Mapping[str, Any]]) -> str:
    """Stable identity of one run's ordered outcome.

    Two runs of the same fixture and seed must produce the same value; a run
    that took a different route -- another pack, another fixture, another
    terminal status -- must not.
    """

    payload = canonical_json(digest_projection(events))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Stream gates
# --------------------------------------------------------------------------

TERMINAL_TYPES: frozenset[str] = frozenset({"run_completed"})
SUPPORTED_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "offline_demo"})


class SurpriseSupportReport(BaseModel):
    """The gate result for one Surprise mission on the structured path."""

    model_config = ConfigDict(frozen=True)

    mission_id: str
    domain: SurpriseDomain
    verdict: SupportVerdict
    passed: bool
    errors: tuple[str, ...] = ()
    terminal_status: str | None = None
    terminal_count: int = 0
    silent_substitution: bool = False
    payment_citations: tuple[str, ...] = ()
    digest: str = ""


def evaluate_support_run(
    support: MissionSupport,
    events: Sequence[Mapping[str, Any]],
) -> SurpriseSupportReport:
    """Gate one complete stream against its supported/unsupported classification.

    The rule that gives this module its name: an unsupported mission must end in
    exactly one typed ``unsupported`` terminal and must carry **no** payment
    evidence.  A run that ends "successfully" with a cake finding after an
    unsupported mission was submitted has not answered the question that was
    asked, and reporting it as a pass is the failure mode issue #69 names.
    """

    errors: list[str] = []
    types = [str(event.get("type", "unknown")) for event in events]

    terminal_indexes = [index for index, kind in enumerate(types) if kind in TERMINAL_TYPES]
    terminal_count = len(terminal_indexes)
    if terminal_count == 0:
        errors.append("stream has no terminal event")
    elif terminal_count > 1:
        errors.append(f"stream has {terminal_count} terminal events, expected exactly one")
    elif terminal_indexes[0] != len(events) - 1:
        errors.append("terminal event is not the last event")

    terminal_status: str | None = None
    if terminal_indexes:
        value = _payload(events[terminal_indexes[-1]]).get("status")
        terminal_status = str(value) if value is not None else None

    sequences = [event.get("seq") for event in events]
    if sequences != list(range(len(events))):
        errors.append("seq is not contiguous from zero")
    run_ids = {event.get("run_id") for event in events if event.get("run_id")}
    if len(run_ids) != 1:
        errors.append("events do not share exactly one run_id")

    citations = payment_evidence(events)
    silent_substitution = False

    if support.verdict is SupportVerdict.UNSUPPORTED:
        if terminal_status != SupportVerdict.UNSUPPORTED.value:
            errors.append(
                f"unsupported mission ended as {terminal_status!r}, expected 'unsupported'"
            )
        finding_statuses = [
            _payload(event).get("status")
            for event, kind in zip(events, types, strict=True)
            if kind == "finding_report"
        ]
        if SupportVerdict.UNSUPPORTED.value not in finding_statuses:
            errors.append("no finding_report carries the unsupported status")
        if citations:
            silent_substitution = True
            errors.append(
                "unsupported mission was answered with payment evidence: "
                + ", ".join(citations)
            )
    elif terminal_status not in SUPPORTED_TERMINAL_STATUSES:
        errors.append(
            f"supported mission ended as {terminal_status!r}, expected one of "
            f"{sorted(SUPPORTED_TERMINAL_STATUSES)}"
        )

    return SurpriseSupportReport(
        mission_id=support.mission_id,
        domain=support.domain,
        verdict=support.verdict,
        passed=not errors,
        errors=tuple(errors),
        terminal_status=terminal_status,
        terminal_count=terminal_count,
        silent_substitution=silent_substitution,
        payment_citations=citations,
        digest=terminal_digest(events),
    )


__all__ = [
    "DOMAIN_REQUIREMENTS",
    "MISSIONS_BY_ID",
    "PAYMENT_FIXTURE_PREFIXES",
    "PAYMENT_TOOLS",
    "SUPPORTED_TERMINAL_STATUSES",
    "SURPRISE_MISSIONS",
    "TERMINAL_TYPES",
    "UNSUPPORTED_REASON",
    "DomainRequirement",
    "MissionSupport",
    "SupportVerdict",
    "SurpriseDomain",
    "SurpriseMission",
    "SurpriseSupportReport",
    "classify_matrix",
    "classify_support",
    "digest_projection",
    "evaluate_support_run",
    "executable_packs",
    "payment_evidence",
    "terminal_digest",
]
