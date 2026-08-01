"""Schema and loader for the executable eval registry (issue #107).

``tests/evals/cases.yaml`` is treated as the canonical record of what this
project checks, but until now nothing read it. A case could name a pytest node
that had been renamed, carry a misspelled field, or duplicate another case's id,
and CI stayed green -- so the registry documented intent without ever proving
it.

This module makes the file a typed artifact. Three things follow from that:

**The schema is closed.** Every case model sets ``extra="forbid"`` and the union
is discriminated on ``runner``, so an unknown field, a missing required field,
or a field belonging to a different runner is a load error rather than a silent
no-op. That is what makes "conflicting field" detectable at all: a ``pytest``
case carrying ``fixture_id`` has no valid parse.

**Targets are structured, not shell.** A case names pytest node ids in a list.
There is no command string to interpolate, no shell to evaluate, and
:data:`NODE_ID_PATTERN` refuses anything that is not a plain node id -- so a
case cannot smuggle an extra pytest flag, a second command, or a path escape
through the registry.

**Every case executes something.** ``pytest`` cases run their targets. The
declarative ``gym_fixture`` and ``runtime_events`` cases run a real harness (see
:mod:`agent24.evals.harness`) *and* may bind their prose invariant to the tests
that prove it via ``proves``. A prose invariant no runner can evaluate is
recorded as prose and pinned to a named test, never presented as if it had been
machined.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SCHEMA_VERSION = "agent24.evals.v1"
"""Bumped when the case schema changes shape. A file declaring another version
is refused rather than parsed on a guess."""

DEFAULT_REGISTRY_PATH = Path("tests/evals/cases.yaml")

CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
"""Case ids appear in CLI arguments and summaries; keep them boring."""

NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_\[\]/.:,+-]+)?$")
"""A pytest node id and nothing else.

Deliberately excludes whitespace, quotes, ``;``, ``&``, ``|``, ``$``, backticks
and ``..``. The runner never uses a shell, so this is defence in depth rather
than the only barrier -- but it also stops a case from passing ``-p`` or
``--capture=no`` as if it were a test.
"""


class RegistryError(ValueError):
    """The registry file could not be loaded as a valid v1 registry."""


class _Case(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    # The one prose field, on every runner: what a reader should conclude if
    # this case passes. Declarative cases put their invariant here rather than
    # carrying a second near-identical string.
    expectation: str = Field(min_length=1)
    issue: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _id_is_boring(self) -> Self:
        if not CASE_ID_PATTERN.match(self.id):
            raise ValueError(
                f"case id {self.id!r} must match {CASE_ID_PATTERN.pattern}"
            )
        return self


def _validate_node_ids(values: Iterable[str], field: str) -> tuple[str, ...]:
    checked: list[str] = []
    for value in values:
        if not NODE_ID_PATTERN.match(value):
            raise ValueError(
                f"{field} entry {value!r} is not a bare pytest node id; "
                "the registry never passes a shell string or an extra flag"
            )
        if ".." in value:
            raise ValueError(f"{field} entry {value!r} may not traverse with '..'")
        checked.append(value)
    return tuple(checked)


class PytestCase(_Case):
    """A claim proved by named pytest nodes."""

    runner: Literal["pytest"]
    targets: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _targets_are_node_ids(self) -> Self:
        _validate_node_ids(self.targets, "targets")
        return self


SITE_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9_./-]+\.(?:mjs|js|ts)$")
"""A path under ``site/`` and nothing else -- same reasoning as node ids."""


class SiteCase(_Case):
    """A claim proved by a Node test under ``site/``.

    Execution needs ``node`` *and* the Vite/Next build output the test imports
    (``site/dist/server/index.js``). Producing that build is the canonical site
    gate's job (#70), not this registry's, so the runner verifies the target
    file exists -- which is the drift this registry is for -- and reports a
    missing prerequisite explicitly instead of passing quietly.
    """

    runner: Literal["site"]
    targets: tuple[str, ...] = Field(min_length=1)
    requires_build: bool = True

    @model_validator(mode="after")
    def _targets_are_paths(self) -> Self:
        for value in self.targets:
            if not SITE_TARGET_PATTERN.match(value) or ".." in value:
                raise ValueError(
                    f"site target {value!r} must be a plain path under site/ "
                    "with no shell syntax and no '..' traversal"
                )
        return self


class ProtectedReplayChecks(BaseModel):
    """Named boolean flags on ``ProtectedReplayReport``.

    Closed on purpose: a typo'd flag name would otherwise be a check that
    silently never runs, which is the failure this registry exists to remove.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool | None = None
    initial_snapshot_match: bool | None = None
    protected_reduces_duplicate_charge: bool | None = None
    protected_mission_succeeds: bool | None = None
    benign_control_succeeds: bool | None = None
    blanket_block_rejected: bool | None = None

    def declared(self) -> dict[str, bool]:
        return {
            name: value
            for name, value in self.model_dump().items()
            if value is not None
        }


class GymFixtureCase(_Case):
    """A synthetic-world fixture the harness loads and checks for real."""

    runner: Literal["gym_fixture"]
    fixture_id: str = Field(min_length=1)
    seed: int = Field(ge=0)
    deterministic_load: bool = True
    fault_type: str | None = None
    protected_replay: ProtectedReplayChecks | None = None
    expected_protected_state: str | None = None
    proves: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _checks_are_reachable(self) -> Self:
        _validate_node_ids(self.proves, "proves")
        if not (self.deterministic_load or self.fault_type or self.protected_replay):
            raise ValueError(
                "a gym_fixture case must check something the harness can evaluate"
            )
        if self.expected_protected_state and self.protected_replay is None:
            raise ValueError(
                "expected_protected_state describes a protected replay result, so "
                "the case must declare protected_replay checks the harness can run"
            )
        return self


class RuntimeEventsCase(_Case):
    """A one-input API run whose event sequence the harness observes."""

    runner: Literal["runtime_events"]
    input: str = Field(min_length=1)
    mode: Literal["offline", "live"]
    expected_event_types: tuple[str, ...] = Field(min_length=1)
    forbid_in_stream: tuple[str, ...] = ()
    proves: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _live_runs_are_bound_to_a_test(self) -> Self:
        _validate_node_ids(self.proves, "proves")
        if self.mode == "live" and not self.proves:
            raise ValueError(
                "a live run needs a mocked OpenAI client, which lives in the test "
                "suite; bind the case to the pytest node that drives it via "
                "'proves' rather than duplicating the stub in the harness"
            )
        return self


Case = Annotated[
    PytestCase | SiteCase | GymFixtureCase | RuntimeEventsCase,
    Field(discriminator="runner"),
]

RUNNERS: tuple[str, ...] = ("pytest", "site", "gym_fixture", "runtime_events")


class Registry(BaseModel):
    """The whole file: a version plus an ordered, unique-id case list."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["agent24.evals.v1"]
    cases: tuple[Case, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        seen: set[str] = set()
        duplicates = sorted({c.id for c in self.cases if c.id in seen or seen.add(c.id)})
        if duplicates:
            raise ValueError(f"duplicate case ids: {', '.join(duplicates)}")
        return self

    def by_id(self) -> dict[str, Case]:
        return {case.id: case for case in self.cases}

    def select(self, ids: Iterable[str]) -> tuple[Case, ...]:
        """Pick cases by id, refusing an id the registry does not define."""

        index = self.by_id()
        wanted = list(ids)
        missing = sorted({i for i in wanted if i not in index})
        if missing:
            raise RegistryError(
                f"unknown case id(s): {', '.join(missing)}. "
                f"known ids: {', '.join(sorted(index))}"
            )
        # Registry order, not argument order, so a selection replays the same
        # way whatever order the caller listed.
        return tuple(case for case in self.cases if case.id in set(wanted))

    def pytest_targets(self) -> Iterator[tuple[str, str]]:
        """Every ``(case_id, node_id)`` the registry expects pytest to collect."""

        for case in self.cases:
            targets = getattr(case, "targets", ()) if isinstance(case, PytestCase) else ()
            proves = getattr(case, "proves", ()) or ()
            for node in (*targets, *proves):
                yield case.id, node

    def site_targets(self) -> Iterator[tuple[str, str]]:
        """Every ``(case_id, path)`` a site case expects to exist under ``site/``."""

        for case in self.cases:
            if isinstance(case, SiteCase):
                for target in case.targets:
                    yield case.id, target


def parse_registry(payload: Any, *, source: str = "<memory>") -> Registry:
    """Validate an already-parsed mapping as a v1 registry."""

    if not isinstance(payload, Mapping):
        raise RegistryError(f"{source}: registry root must be a mapping")
    declared = payload.get("version")
    if declared != SCHEMA_VERSION:
        raise RegistryError(
            f"{source}: registry version {declared!r} is not {SCHEMA_VERSION!r}; "
            "refusing to guess an older layout"
        )
    try:
        return Registry.model_validate(dict(payload))
    except ValidationError as error:
        raise RegistryError(f"{source}: {error}") from error


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> Registry:
    """Read and validate the registry file.

    ``yaml.safe_load`` only: the registry is data, and a full loader would let
    the file name Python objects to construct.
    """

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as error:
        raise RegistryError(f"cannot read registry at {source}: {error}") from error
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise RegistryError(f"{source}: invalid YAML: {error}") from error
    return parse_registry(payload, source=str(source))


__all__ = [
    "CASE_ID_PATTERN",
    "DEFAULT_REGISTRY_PATH",
    "NODE_ID_PATTERN",
    "RUNNERS",
    "SCHEMA_VERSION",
    "Case",
    "GymFixtureCase",
    "ProtectedReplayChecks",
    "PytestCase",
    "Registry",
    "SITE_TARGET_PATTERN",
    "SiteCase",
    "RegistryError",
    "RuntimeEventsCase",
    "load_registry",
    "parse_registry",
]
