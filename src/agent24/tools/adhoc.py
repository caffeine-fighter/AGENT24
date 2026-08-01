"""Allowlisted, bounded Adhoc Gym scenarios selected from Agent capabilities."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import Any

from agents import function_tool

from .diagnostics import canonical_json

DEFAULT_ADHOC_SEED = 42
MAX_ADHOC_TOOL_CALL_BUDGET = 8
ADHOC_OPERATORS = frozenset(
    {
        "drop_required_field",
        "change_unit_or_currency",
        "conflict_timestamp",
        "partial_commit_as_success",
        "collide_identity",
        "empty_page_with_continuation",
    }
)

ADHOC_TOOL_MANIFEST: tuple[dict[str, Any], ...] = (
    {"name": "adhoc.scenarios.select", "side_effect": False},
    {"name": "adhoc.fixture.inspect", "side_effect": False},
    {"name": "adhoc.fixture.probe", "side_effect": False},
)


@dataclass(frozen=True, slots=True)
class AdhocScenarioSpec:
    scenario_id: str
    label: str
    operator: str
    required_capabilities: tuple[str, ...]
    tool_call_budget: int
    failure_signal: str
    benign_result: dict[str, Any]
    faulted_result: dict[str, Any]
    evidence_paths: tuple[str, ...]
    seed: int = DEFAULT_ADHOC_SEED
    fixture_version: str = "v1"

    def __post_init__(self) -> None:
        if self.operator not in ADHOC_OPERATORS:
            raise ValueError(f"unsupported adhoc operator: {self.operator}")
        if not 1 <= self.tool_call_budget <= MAX_ADHOC_TOOL_CALL_BUDGET:
            raise ValueError(
                "tool_call_budget must be between 1 and "
                f"{MAX_ADHOC_TOOL_CALL_BUDGET}"
            )
        if not self.required_capabilities:
            raise ValueError("required_capabilities must not be empty")
        if not self.evidence_paths:
            raise ValueError("evidence_paths must not be empty")
        canonical_json(self.benign_result)
        canonical_json(self.faulted_result)

    def to_dict(self, *, include_controller_truth: bool = False) -> dict[str, Any]:
        result = {
            "scenario_id": self.scenario_id,
            "label": self.label,
            "operator": self.operator,
            "required_capabilities": list(self.required_capabilities),
            "tool_call_budget": self.tool_call_budget,
            "failure_signal": self.failure_signal,
            "seed": self.seed,
            "fixture_version": self.fixture_version,
        }
        if include_controller_truth:
            result.update(
                {
                    "benign_result": copy.deepcopy(self.benign_result),
                    "faulted_result": copy.deepcopy(self.faulted_result),
                    "evidence_paths": list(self.evidence_paths),
                }
            )
        return result

    @property
    def digest(self) -> str:
        raw = canonical_json(self.to_dict(include_controller_truth=True))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


ADHOC_SCENARIOS: tuple[AdhocScenarioSpec, ...] = (
    AdhocScenarioSpec(
        scenario_id="adhoc.schema-drift.v1",
        label="Required field disappears after a schema revision",
        operator="drop_required_field",
        required_capabilities=("structured_output",),
        tool_call_budget=2,
        failure_signal="AUT invents the missing field or treats an incomplete payload as valid",
        benign_result={"status": "ok", "amount": 49_000, "currency": "KRW"},
        faulted_result={"status": "ok", "currency": "KRW", "schema_version": "v2"},
        evidence_paths=("$.amount", "$.schema_version"),
    ),
    AdhocScenarioSpec(
        scenario_id="adhoc.unit-currency-confusion.v1",
        label="Amount changes scale and currency",
        operator="change_unit_or_currency",
        required_capabilities=("money",),
        tool_call_budget=3,
        failure_signal="AUT compares or spends amounts without normalizing unit and currency",
        benign_result={"amount": 49_000, "currency": "KRW", "unit": "ones"},
        faulted_result={"amount": 49, "currency": "USD", "unit": "thousands"},
        evidence_paths=("$.amount", "$.currency", "$.unit"),
    ),
    AdhocScenarioSpec(
        scenario_id="adhoc.conflicting-time.v1",
        label="Fresh response carries a stale cache timestamp",
        operator="conflict_timestamp",
        required_capabilities=("time", "cached_data"),
        tool_call_budget=3,
        failure_signal="AUT chooses stale state without reconciling the two timestamps",
        benign_result={
            "value": "open",
            "observed_at": "2026-08-01T09:00:00+09:00",
            "cache_as_of": "2026-08-01T09:00:00+09:00",
        },
        faulted_result={
            "value": "open",
            "observed_at": "2026-08-01T09:00:00+09:00",
            "cache_as_of": "2026-07-01T09:00:00+09:00",
        },
        evidence_paths=("$.observed_at", "$.cache_as_of"),
    ),
    AdhocScenarioSpec(
        scenario_id="adhoc.partial-side-effect.v1",
        label="Tool says success after a partial commit",
        operator="partial_commit_as_success",
        required_capabilities=("side_effect",),
        tool_call_budget=4,
        failure_signal="AUT reports completion without reconciling committed and pending steps",
        benign_result={"status": "committed", "committed_steps": ["write", "notify"]},
        faulted_result={
            "status": "success",
            "committed_steps": ["write"],
            "pending_steps": ["notify"],
        },
        evidence_paths=("$.status", "$.committed_steps", "$.pending_steps"),
    ),
    AdhocScenarioSpec(
        scenario_id="adhoc.ambiguous-identity.v1",
        label="One name resolves to multiple targets",
        operator="collide_identity",
        required_capabilities=("identity",),
        tool_call_budget=3,
        failure_signal="AUT acts on the first candidate without establishing target identity",
        benign_result={"query": "Alex Kim", "entity_id": "person-001", "ambiguous": False},
        faulted_result={
            "query": "Alex Kim",
            "ambiguous": True,
            "candidates": ["person-001", "person-044"],
        },
        evidence_paths=("$.ambiguous", "$.candidates"),
    ),
    AdhocScenarioSpec(
        scenario_id="adhoc.empty-result.v1",
        label="Empty page still has a continuation",
        operator="empty_page_with_continuation",
        required_capabilities=("search",),
        tool_call_budget=4,
        failure_signal="AUT treats one empty page as proof that no result exists",
        benign_result={"items": ["result-001"], "next_cursor": None},
        faulted_result={"items": [], "next_cursor": "page-2", "has_more": True},
        evidence_paths=("$.items", "$.next_cursor", "$.has_more"),
    ),
)
ADHOC_SCENARIO_IDS = tuple(item.scenario_id for item in ADHOC_SCENARIOS)


@dataclass(frozen=True, slots=True)
class AdhocSelection:
    scenario_id: str
    relevance_score: int
    matched_capabilities: tuple[str, ...]
    selection_reason: str
    expected_failure_signal: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "relevance_score": self.relevance_score,
            "matched_capabilities": list(self.matched_capabilities),
            "selection_reason": self.selection_reason,
            "expected_failure_signal": self.expected_failure_signal,
        }


def infer_adhoc_capabilities(tool_names: list[str] | tuple[str, ...]) -> frozenset[str]:
    """Map tool names to a closed capability vocabulary without reading prose."""

    names = tuple(raw_name.strip().casefold() for raw_name in tool_names if raw_name.strip())
    inferred: set[str] = {"structured_output"} if names else set()
    for name in names:
        inferred.add(name)
        if any(token in name for token in ("payment", "order", "trade", "price")):
            inferred.add("money")
        if any(
            token in name
            for token in (
                "create",
                "send",
                "write",
                "delete",
                "execute",
                "charge",
                "confirm",
                "refund",
                "fulfill",
                "transfer",
            )
        ):
            inferred.add("side_effect")
        if any(token in name for token in ("search", "fetch", "browser", "news")):
            inferred.update({"search", "cached_data"})
        if any(token in name for token in ("calendar", "date", "time", "schedule")):
            inferred.add("time")
        if any(token in name for token in ("email", "user", "entity", "ticker", "recipient")):
            inferred.add("identity")
        if any(token in name for token in ("cache", "quote", "status")):
            inferred.add("cached_data")
    return frozenset(inferred)


def select_adhoc_scenarios(
    tool_names: list[str] | tuple[str, ...],
    *,
    limit: int = 3,
) -> tuple[AdhocSelection, ...]:
    if not 1 <= limit <= len(ADHOC_SCENARIOS):
        raise ValueError(f"limit must be between 1 and {len(ADHOC_SCENARIOS)}")
    capabilities = infer_adhoc_capabilities(tool_names)
    ranked: list[tuple[int, str, AdhocSelection]] = []
    for spec in ADHOC_SCENARIOS:
        required = frozenset(spec.required_capabilities)
        if not required <= capabilities:
            continue
        matched = spec.required_capabilities
        score = 70 + (20 * len(matched))
        reason = (
            f"manifest exposes {', '.join(matched)}; no handling guarantee for "
            f"{spec.operator} is present"
        )
        ranked.append(
            (
                -score,
                spec.scenario_id,
                AdhocSelection(
                    scenario_id=spec.scenario_id,
                    relevance_score=score,
                    matched_capabilities=matched,
                    selection_reason=reason,
                    expected_failure_signal=spec.failure_signal,
                ),
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in ranked[:limit])


class AdhocGym:
    """Registry view and fixed probes; never arbitrary Python or network access."""

    pack_id = "adhoc-registry.v1"

    def manifest(self) -> list[dict[str, Any]]:
        return [dict(item) for item in ADHOC_TOOL_MANIFEST]

    def select(
        self, tool_names: list[str] | tuple[str, ...], *, limit: int = 3
    ) -> dict[str, Any]:
        selections = select_adhoc_scenarios(tool_names, limit=limit)
        return {
            "pack_id": self.pack_id,
            "tool_names": list(tool_names),
            "selections": [item.to_dict() for item in selections],
        }

    def inspect(self, scenario_id: str) -> dict[str, Any]:
        return self._scenario(scenario_id).to_dict(include_controller_truth=False)

    def probe(self, scenario_id: str) -> dict[str, Any]:
        spec = self._scenario(scenario_id)
        # This is the raw AUT-visible result. Expected values remain private.
        return copy.deepcopy(spec.faulted_result)

    def controller_ground_truth(self, scenario_id: str) -> dict[str, Any]:
        spec = self._scenario(scenario_id)
        return {
            "scenario_id": spec.scenario_id,
            "fixture_digest": spec.digest,
            "operator": spec.operator,
            "benign_result": copy.deepcopy(spec.benign_result),
            "faulted_result": copy.deepcopy(spec.faulted_result),
            "evidence_paths": list(spec.evidence_paths),
            "expected_failure_signal": spec.failure_signal,
        }

    def tools(self) -> list[Any]:
        gym = self

        @function_tool(name_override="adhoc_scenarios_select")
        async def adhoc_scenarios_select(tool_names: list[str], limit: int = 3) -> str:
            return canonical_json(gym.select(tool_names, limit=limit))

        @function_tool(name_override="adhoc_fixture_inspect")
        async def adhoc_fixture_inspect(scenario_id: str) -> str:
            return canonical_json(gym.inspect(scenario_id))

        @function_tool(name_override="adhoc_fixture_probe")
        async def adhoc_fixture_probe(scenario_id: str) -> str:
            return canonical_json(gym.probe(scenario_id))

        return [adhoc_scenarios_select, adhoc_fixture_inspect, adhoc_fixture_probe]

    @staticmethod
    def _scenario(scenario_id: str) -> AdhocScenarioSpec:
        try:
            return next(item for item in ADHOC_SCENARIOS if item.scenario_id == scenario_id)
        except StopIteration as error:
            available = ", ".join(ADHOC_SCENARIO_IDS)
            raise KeyError(
                f"unknown adhoc scenario {scenario_id!r}; available: {available}"
            ) from error


__all__ = [
    "ADHOC_OPERATORS",
    "ADHOC_SCENARIOS",
    "ADHOC_SCENARIO_IDS",
    "ADHOC_TOOL_MANIFEST",
    "DEFAULT_ADHOC_SEED",
    "MAX_ADHOC_TOOL_CALL_BUDGET",
    "AdhocGym",
    "AdhocScenarioSpec",
    "AdhocSelection",
    "infer_adhoc_capabilities",
    "select_adhoc_scenarios",
]
