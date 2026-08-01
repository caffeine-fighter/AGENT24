"""Domain pack registry and the deterministic router that picks one.

``planner.py`` chooses a *fault operator* inside the Life-v0 gym. This module
sits one level above it and chooses *which gym* to run at all: Life, Research,
Stock, Ticket or Adhoc. The packs already exist under :mod:`agent24.tools`; this
is the typed seam that connects them to one Lab Agent controller, not a
reimplementation of any of them.

**Why the specs are data rather than imports.** Every pack gym
(``SandboxGym``, ``ResearchGym``, ``StockGym``, ``AdhocGym``) imports
``agents.function_tool`` at module scope, and this package must not import
``openai`` or ``agents`` -- see ``agent/README.md``. So a
:class:`DomainPackSpec` describes a pack in plain data and never holds the class
that implements it. The obvious risk of restating a vocabulary is that it drifts
from the pack it describes, so ``tests/unit/test_packs.py`` imports the real
packs and asserts every declared tool set, fixture id and fault family matches
the pack itself. The duplication is checked, not trusted.

**What the router decides, and what it refuses to decide.** Selection is a pure
function of ``(manifest, profile, mission)`` -- no clock, no randomness, no
network. Two rules from the issue shape it:

* A pack whose required tool surface is absent is never selected. Approximating
  a Research agent with a payment experiment would be inventing a finding.
* A tie between two domain packs is *reported*, not broken. When the evidence
  does not discriminate, saying so is the honest answer; silently taking the
  lexically-first pack would dress a coin flip up as a decision.

Life, Research, and Stock are wired into the one-input controller. Ticket and
Adhoc remain registered-only until their controller/report bridges are added. A
selected-but-not-executable pack stops with
:class:`~agent24.agent.models.StopDecision`, and never falls through to another
pack's success.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    AgentCard,
    Mission,
    MissionFamily,
    StopDecision,
    canonical_json,
)
from .profile import AgentManifest, BehaviorProfile, EvidenceRef
from .prompts import LoopBudget

REGISTRY_VERSION = "domain-pack-registry.v1"
"""Bumped when a spec changes. Part of the selection digest, so a registry edit
is visible in the replay rather than silently changing a past run's meaning."""


class DomainKind(StrEnum):
    """Which synthetic world a pack models.

    Deliberately separate from :class:`~agent24.agent.models.MissionFamily`.
    A mission family describes what the *user asked for* (purchase, research);
    a domain kind describes which *gym* can stage it. Overloading one enum with
    both would make ``MissionFamily.RESEARCH`` mean two different things, and
    Stock has no mission family at all today.
    """
    LIFE = "life"
    RESEARCH = "research"
    STOCK = "stock"
    TICKET = "ticket"
    ADHOC = "adhoc"


_ALLOWLISTED_ADAPTER_ALIASES: dict[str, str] = {
    "get_available_products": "web.fetch",
    "get_available_discount_codes": "web.fetch",
    "discover_merchant": "web.fetch",
    "create_cart": "order.create",
    "apply_discount": "order.create",
    "set_shipping_address": "order.create",
    "complete_purchase": "payment.charge",
}


class PackExecution(StrEnum):
    """Whether selecting this pack can actually run anything yet."""

    ONE_INPUT_CONTROLLER = "one_input_controller"
    REGISTERED = "registered"


class PackBudget(BaseModel):
    """The bound a pack run may not exceed, declared per pack."""

    model_config = ConfigDict(frozen=True)

    max_tool_calls: int = Field(ge=1)
    max_experiments: int = Field(ge=1)
    max_cost_units: int = Field(ge=1)

    def loop_budget(self) -> LoopBudget:
        """Bridge to the loop's stop conditions so there is one budget, not two."""

        return LoopBudget(
            max_experiments=self.max_experiments,
            max_cost_units=self.max_cost_units,
        )


class DomainPackSpec(BaseModel):
    """Everything the router needs to know about one pack, as data.

    ``anchor_tools`` is an *any-of* set: at least one must be present for the
    pack to be a candidate at all. ``required_tools`` is an *all-of* set, empty
    for every pack today but kept because a pack whose experiments genuinely
    need a pair of tools has no other way to say so. ``optional_tools`` only
    raises coverage.
    """

    model_config = ConfigDict(frozen=True)

    pack_id: str
    version: str
    domain_kind: DomainKind
    mission_families: frozenset[MissionFamily] = frozenset()
    anchor_tools: frozenset[str] = frozenset()
    required_tools: frozenset[str] = frozenset()
    optional_tools: frozenset[str] = frozenset()
    fixture_ids: tuple[str, ...] = ()
    fault_families: frozenset[str] = frozenset()
    gated_capabilities: tuple[str, ...] = ()
    expected_damage: int = Field(ge=1, le=5)
    budget: PackBudget
    # Both flags describe what the *pack* can do. ``execution`` and
    # ``deferred_to`` answer whether the one-input controller can drive it.
    supports_benign_control: bool = False
    supports_protected_replay: bool = False
    execution: PackExecution = PackExecution.REGISTERED
    deferred_to: str = ""
    is_fallback: bool = False

    @property
    def executable(self) -> bool:
        return self.execution is PackExecution.ONE_INPUT_CONTROLLER

    @property
    def all_tools(self) -> frozenset[str]:
        return self.anchor_tools | self.required_tools | self.optional_tools


# --------------------------------------------------------------------------
# The registry.
#
# Every tool name, fixture id and fault family below is asserted against the
# real pack in tests/unit/test_packs.py.  Change a pack, and that test fails
# before this file becomes a lie.
# --------------------------------------------------------------------------

LIFE_PACK = DomainPackSpec(
    pack_id="life-v0-sandbox.v1",
    version="v1",
    domain_kind=DomainKind.LIFE,
    mission_families=frozenset(
        {MissionFamily.PURCHASE, MissionFamily.EMAIL, MissionFamily.CALENDAR}
    ),
    anchor_tools=frozenset(
        {"payment.charge", "payment_intent.confirm", "order.create", "web.fetch", "web.search"}
    ),
    optional_tools=frozenset(
        {
            "catalog.search",
            "payment.status",
            "payment_intent.create",
            "payment_intent.retrieve",
            "webhook.deliver",
            "order.fulfill",
            "email.send",
            "calendar.create",
            "file.write",
        }
    ),
    fixture_ids=(
        "life.cake_collision.v1",
        "life.empty_search.v1",
        "life.payment_intent_timeout.v1",
        "life.web_injection.v1",
        "life.webhook_duplicate.v1",
        "life.webhook_out_of_order.v1",
    ),
    fault_families=frozenset(
        {
            "commit_then_timeout",
            "duplicate_webhook",
            "empty_result",
            "malicious_web_content",
            "out_of_order_webhook",
        }
    ),
    gated_capabilities=("idempotency_usage", "untrusted_input_handling", "loop_budget"),
    expected_damage=5,
    budget=PackBudget(max_tool_calls=20, max_experiments=3, max_cost_units=6),
    supports_benign_control=True,
    supports_protected_replay=True,
    execution=PackExecution.ONE_INPUT_CONTROLLER,
)

RESEARCH_PACK = DomainPackSpec(
    pack_id="research-agent-pack.v1",
    version="v1",
    domain_kind=DomainKind.RESEARCH,
    mission_families=frozenset({MissionFamily.RESEARCH}),
    anchor_tools=frozenset(
        {"research.search", "paper.fetch", "citation.resolve", "pdf.page.read", "table.read"}
    ),
    optional_tools=frozenset(
        {"repository.inspect", "dataset.inspect", "experiment.inspect"}
    ),
    fixture_ids=(
        "research.abstract-table-contradiction.v1",
        "research.clean-control.v1",
        "research.full-gauntlet.v1",
        "research.generated-citation.v1",
        "research.pdf-prompt-injection.v1",
        "research.reproducibility-mirage.v1",
    ),
    fault_families=frozenset(
        {
            "abstract_table_contradiction",
            "generated_citation",
            "pdf_prompt_injection",
            "reproducibility_mirage",
        }
    ),
    gated_capabilities=("untrusted_input_handling",),
    expected_damage=4,
    budget=PackBudget(max_tool_calls=12, max_experiments=3, max_cost_units=6),
    supports_benign_control=True,
    supports_protected_replay=True,
    execution=PackExecution.ONE_INPUT_CONTROLLER,
)

STOCK_PACK = DomainPackSpec(
    pack_id="stock-analyst-pack.v1",
    version="v1",
    domain_kind=DomainKind.STOCK,
    # No MissionFamily.STOCK exists, and adding one would edit the frozen
    # models.  Stock is discriminated by its tool surface instead, which is what
    # the issue asks for: a market tool manifest selects the Stock pack.
    mission_families=frozenset(),
    anchor_tools=frozenset(
        {"ticker.resolve", "market.disclosures", "market.news", "market.record"}
    ),
    optional_tools=frozenset({"market.source", "entity.relations", "analyst_note.read"}),
    fixture_ids=(
        "stock.analyst-note-injection.v1",
        "stock.clean-control.v1",
        "stock.entity-mixing.v1",
        "stock.full-gauntlet.v1",
        "stock.rumor-vs-disclosure.v1",
        "stock.source-misattribution.v1",
        "stock.stale-truth.v1",
        "stock.ticker-collision.v1",
    ),
    fault_families=frozenset(
        {
            "analyst_note_injection",
            "entity_mixing",
            "rumor_vs_disclosure",
            "source_misattribution",
            "stale_truth",
            "ticker_collision",
        }
    ),
    gated_capabilities=("untrusted_input_handling",),
    expected_damage=4,
    budget=PackBudget(max_tool_calls=14, max_experiments=3, max_cost_units=6),
    supports_benign_control=True,
    supports_protected_replay=True,
    execution=PackExecution.ONE_INPUT_CONTROLLER,
)

TICKET_PACK = DomainPackSpec(
    pack_id="ticket-purchase-pack.v1",
    version="v1",
    domain_kind=DomainKind.TICKET,
    # Mirrors tools/ticket_pack.py::TICKET_PACK_METADATA, which #60 wrote for
    # exactly this purpose.  The placeholder vocabulary this replaced
    # (ticket.search / ticket.hold / reservation.*) shared *nothing* with the
    # shipped gym, so a real ticket agent fell through to the Adhoc fallback.
    mission_families=frozenset({MissionFamily.PURCHASE}),
    anchor_tools=frozenset(
        {
            "ticket.event.search",
            "ticket.inventory.read",
            "ticket.hold.create",
            "ticket.purchase.confirm",
        }
    ),
    # The pack declares these four as required, not merely useful: it cannot
    # stage a hold-then-purchase failure without the whole chain.
    required_tools=frozenset(
        {
            "ticket.event.search",
            "ticket.inventory.read",
            "ticket.hold.create",
            "ticket.purchase.confirm",
        }
    ),
    optional_tools=frozenset(
        {
            "ticket.hold.retrieve",
            "ticket.booking.retrieve",
            "ticket.hold.cancel",
            "ticket.booking.cancel",
        }
    ),
    fixture_ids=(
        "ticket.cancel-ambiguity.clean.v1",
        "ticket.cancel-ambiguity.v1",
        "ticket.clean-control.v1",
        "ticket.commit-then-timeout.clean.v1",
        "ticket.commit-then-timeout.v1",
        "ticket.event-identity-confusion.clean.v1",
        "ticket.event-identity-confusion.v1",
        "ticket.full-demo.v1",
        "ticket.price-fee-currency-drift.clean.v1",
        "ticket.price-fee-currency-drift.v1",
        "ticket.quantity-adjacency-violation.clean.v1",
        "ticket.quantity-adjacency-violation.v1",
        "ticket.stale-availability.clean.v1",
        "ticket.stale-availability.v1",
    ),
    fault_families=frozenset(
        {
            "cancel_ambiguity",
            "commit_then_timeout",
            "event_identity_confusion",
            "price_fee_currency_drift",
            "quantity_adjacency",
            "stale_availability",
        }
    ),
    gated_capabilities=("idempotency_usage", "reconciliation_usage"),
    expected_damage=5,
    budget=PackBudget(max_tool_calls=16, max_experiments=3, max_cost_units=6),
    supports_benign_control=True,
    supports_protected_replay=True,
    # #60 shipped the gym, adapter and protected replay.  What is still missing
    # is the one-input controller wiring, which the epic owns -- the same
    # position Adhoc is in.
    deferred_to="#55",
)

ADHOC_PACK = DomainPackSpec(
    pack_id="adhoc-registry.v1",
    version="v1",
    domain_kind=DomainKind.ADHOC,
    # The fallback matches on inferred capabilities rather than a fixed surface,
    # so it declares no anchor and is only reachable when no domain pack is.
    mission_families=frozenset(),
    fixture_ids=(
        "adhoc.ambiguous-identity.v1",
        "adhoc.conflicting-time.v1",
        "adhoc.empty-result.v1",
        "adhoc.partial-side-effect.v1",
        "adhoc.schema-drift.v1",
        "adhoc.unit-currency-confusion.v1",
    ),
    fault_families=frozenset(
        {
            "change_unit_or_currency",
            "collide_identity",
            "conflict_timestamp",
            "drop_required_field",
            "empty_page_with_continuation",
            "partial_commit_as_success",
        }
    ),
    gated_capabilities=("retry_behavior", "loop_budget"),
    expected_damage=3,
    budget=PackBudget(max_tool_calls=8, max_experiments=3, max_cost_units=6),
    supports_benign_control=True,
    # The pack itself shipped in #44; what is missing is its one-input
    # controller wiring, which the epic still owns.
    deferred_to="#55",
    is_fallback=True,
)

DOMAIN_PACKS: tuple[DomainPackSpec, ...] = (
    LIFE_PACK,
    RESEARCH_PACK,
    STOCK_PACK,
    TICKET_PACK,
    ADHOC_PACK,
)

PACKS_BY_ID: dict[str, DomainPackSpec] = {spec.pack_id: spec for spec in DOMAIN_PACKS}
PACKS_BY_KIND: dict[DomainKind, DomainPackSpec] = {
    spec.domain_kind: spec for spec in DOMAIN_PACKS
}

ROUTABLE_TOOLS: frozenset[str] = frozenset(
    tool for spec in DOMAIN_PACKS for tool in spec.all_tools
)
"""Every tool name any pack can route on. A manifest naming none of these is
honestly unroutable rather than quietly handed to the fallback."""


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

_ANCHOR_WEIGHT = 3
_FAMILY_WEIGHT = 4
_MEASURED_GATE_WEIGHT = 2
_UNKNOWN_GATE_WEIGHT = 1


class PackCandidate(BaseModel):
    """One pack scored against one agent, with the reasoning kept."""

    model_config = ConfigDict(frozen=True)

    pack_id: str
    domain_kind: DomainKind
    score: int
    matched_anchors: tuple[str, ...]
    matched_optional: tuple[str, ...]
    missing_required: tuple[str, ...]
    family_match: bool
    measured_gates: tuple[str, ...]
    unknown_gates: tuple[str, ...]
    is_fallback: bool
    executable: bool
    why: str

    @property
    def spec(self) -> DomainPackSpec:
        return PACKS_BY_ID[self.pack_id]


class PackSelection(BaseModel):
    """The routing decision, with why / expect / evidence / budget / fallback.

    ``stop`` is ``None`` only when the selected pack can actually be executed by
    the one-input controller. Everything else -- ambiguous, unroutable, or a
    pack whose execution lands in a later issue -- carries the honest stop the
    caller must respect.
    """

    model_config = ConfigDict(frozen=True)

    registry_version: str = REGISTRY_VERSION
    selected: PackCandidate | None = None
    candidates: tuple[PackCandidate, ...] = ()
    why: str = ""
    expect: str = ""
    evidence: tuple[EvidenceRef, ...] = ()
    budget: PackBudget | None = None
    fallback: str = ""
    stop: StopDecision | None = None
    selection_digest: str = ""

    @property
    def pack_id(self) -> str:
        return self.selected.pack_id if self.selected else ""

    @property
    def executed_by_controller(self) -> bool:
        return self.stop is None


def selection_digest(selection: PackSelection) -> str:
    """Stable identity for a routing decision, excluding the digest itself."""

    payload = selection.model_dump(mode="json")
    payload.pop("selection_digest", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _stamp(selection: PackSelection) -> PackSelection:
    return selection.model_copy(update={"selection_digest": selection_digest(selection)})


def _flagged(manifest: AgentManifest) -> set[str]:
    """Tool names the manifest loader refused, without their reason prefix.

    ``load_manifest`` keeps a rejected tool in ``tools`` *and* records it in
    ``unsupported_tools`` as ``"unsupported:<name>"`` / ``"duplicate:<name>"``.
    Routing on one would mean planning an experiment against a tool no gym can
    drive, so they are removed from the routable surface entirely.
    """

    return {entry.split(":", 1)[-1] for entry in manifest.unsupported_tools}


def _tools_of(
    manifest: AgentManifest, profile: BehaviorProfile, card: AgentCard | None
) -> set[str]:
    tools = {capability.tool for capability in profile.capabilities}
    tools |= {tool.name for tool in manifest.tools}
    if card is not None:
        tools |= {tool.name for tool in card.tools}
    tools -= _flagged(manifest)
    if manifest.adapter_version == "ucp-shopping-v0":
        tools.update(
            _ALLOWLISTED_ADAPTER_ALIASES[tool]
            for tool in tuple(tools)
            if tool in _ALLOWLISTED_ADAPTER_ALIASES
        )
    return tools


def _gate_split(
    spec: DomainPackSpec, profile: BehaviorProfile
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a pack's gate capabilities into measured weaknesses and gaps.

    Mirrors ``planner.OperatorCandidate.gate_priority``: an ``absent`` verdict is
    a capability we watched fail, an ``unknown`` is one we never observed. Both
    are reasons to probe; only the first is evidence.
    """

    assessments = profile.assessments()
    measured = tuple(
        name
        for name in spec.gated_capabilities
        if name in assessments and assessments[name].value == "absent"
    )
    gaps = tuple(
        name
        for name in spec.gated_capabilities
        if name in assessments and assessments[name].value == "unknown"
    )
    return measured, gaps


def _describe(spec: DomainPackSpec, anchors: Sequence[str], family_match: bool,
              measured: Sequence[str], gaps: Sequence[str], score: int) -> str:
    parts = [f"{spec.pack_id} 점수 {score}"]
    if anchors:
        parts.append(f"anchor 도구 {', '.join(anchors)} 관찰")
    if family_match:
        parts.append("mission family 일치")
    if measured:
        parts.append(f"측정된 취약 지점 {', '.join(measured)}(absent)")
    if gaps:
        parts.append(f"관찰되지 않은 공백 {', '.join(gaps)}(unknown)")
    if spec.is_fallback:
        parts.append("도메인 pack이 없을 때만 쓰는 fallback")
    return "; ".join(parts) + "."


def score_packs(
    *,
    manifest: AgentManifest,
    profile: BehaviorProfile,
    mission: Mission,
    card: AgentCard | None = None,
) -> list[PackCandidate]:
    """Score every pack this agent's surface makes reachable, deterministically.

    Ordering is total and reproducible: domain packs before the fallback, then
    descending score, then ``pack_id``. Nothing consults a clock, a random
    source or free text -- selection reads the declared tool surface, the
    mission family and the profile's own verdicts.
    """

    tools = _tools_of(manifest, profile, card)
    candidates: list[PackCandidate] = []

    for spec in DOMAIN_PACKS:
        missing_required = tuple(sorted(spec.required_tools - tools))
        if missing_required:
            continue
        anchors = tuple(sorted(spec.anchor_tools & tools))
        if spec.anchor_tools and not anchors:
            # The pack's surface is simply not here. Selecting it anyway is how
            # a Research agent ends up "tested" with a payment experiment.
            continue
        if spec.is_fallback and not tools:
            # The fallback matches any tool surface, but "any" is not "none":
            # an agent that declares no tools has nothing to probe, and routing
            # it to Adhoc would turn an empty manifest into a plausible plan.
            continue

        optional = tuple(sorted(spec.optional_tools & tools))
        family_match = mission.family in spec.mission_families
        measured, gaps = _gate_split(spec, profile)

        score = (
            _ANCHOR_WEIGHT * len(anchors)
            + len(optional)
            + (_FAMILY_WEIGHT if family_match else 0)
            + _MEASURED_GATE_WEIGHT * len(measured)
            + _UNKNOWN_GATE_WEIGHT * len(gaps)
            + spec.expected_damage
        )

        candidates.append(
            PackCandidate(
                pack_id=spec.pack_id,
                domain_kind=spec.domain_kind,
                score=score,
                matched_anchors=anchors,
                matched_optional=optional,
                missing_required=missing_required,
                family_match=family_match,
                measured_gates=measured,
                unknown_gates=gaps,
                is_fallback=spec.is_fallback,
                executable=spec.executable,
                why=_describe(spec, anchors, family_match, measured, gaps, score),
            )
        )

    candidates.sort(key=lambda c: (c.is_fallback, -c.score, c.pack_id))
    return candidates


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def _evidence_for(
    spec: DomainPackSpec, manifest: AgentManifest, profile: BehaviorProfile
) -> tuple[EvidenceRef, ...]:
    """Cite where the routing decision came from, in the decision itself."""

    refs: list[EvidenceRef] = [
        EvidenceRef(
            kind="manifest",
            path="tools",
            detail=f"{manifest.name} declares {len(manifest.tools)} allowlisted tools",
        )
    ]
    assessments = profile.assessments()
    for name in spec.gated_capabilities:
        assessment = assessments.get(name)
        if assessment is None:
            continue
        if assessment.value == "unknown":
            refs.append(
                EvidenceRef(
                    kind="manifest",
                    path=f"profile.{name}",
                    detail=f"unknown — {assessment.unknown_reason}",
                )
            )
            continue
        refs.extend(assessment.evidence)
    return tuple(refs)


def _expect_for(spec: DomainPackSpec) -> str:
    if not spec.fault_families:
        return (
            f"{spec.pack_id}는 아직 재현할 fault family를 등록하지 않았다. "
            f"실행 경로는 {spec.deferred_to}가 제공한다."
        )
    families = ", ".join(sorted(spec.fault_families))
    return (
        f"{spec.pack_id} 실행 시 {families} 중 하나가 재현되어야 가설이 지지된다. "
        f"예산: 도구 호출 {spec.budget.max_tool_calls}회, 실험 "
        f"{spec.budget.max_experiments}회, 비용 {spec.budget.max_cost_units} 단위."
    )


def _fallback_for(spec: DomainPackSpec, candidates: Sequence[PackCandidate]) -> str:
    others = [c.pack_id for c in candidates if c.pack_id != spec.pack_id]
    if not others:
        return "대체 pack이 없다. 실패 시 정직하게 unsupported로 종료한다."
    return (
        f"{spec.pack_id} 실행이 실패하면 {', '.join(others)}의 성공으로 대체하지 않고 "
        "실패한 pack을 명시해 종료한다."
    )


def unroutable(manifest: AgentManifest, profile: BehaviorProfile) -> StopDecision:
    """No pack's surface is present. Say that, rather than testing something else."""

    routable = sorted(_tools_of(manifest, profile, None))
    flagged = sorted(_flagged(manifest))

    if not routable and flagged:
        detail = (
            f"도구 {', '.join(flagged)}가 지원 어휘 밖이라 실행할 수 있는 domain pack이 없다. "
            f"라우팅 가능한 도구: {', '.join(sorted(ROUTABLE_TOOLS))}."
        )
    elif not routable:
        detail = "manifest에 도구가 없어 선택할 수 있는 domain pack이 없다."
    else:
        detail = (
            f"도구 {', '.join(routable)}가 등록된 어느 pack의 surface에도 해당하지 않는다. "
            f"라우팅 가능한 도구: {', '.join(sorted(ROUTABLE_TOOLS))}."
        )
    return StopDecision(stop=True, reason="unsupported_input", detail=detail)


def _ambiguous(tied: Sequence[PackCandidate]) -> StopDecision:
    """A tie is a result, not a coin flip.

    ``insufficient_evidence`` rather than ``unsupported_input``: the input is
    supported, the evidence just does not say which pack owns it.
    """

    names = ", ".join(candidate.pack_id for candidate in tied)
    return StopDecision(
        stop=True,
        reason="insufficient_evidence",
        detail=(
            f"pack {names}가 동일 점수 {tied[0].score}로 동점이라 관찰된 근거만으로는 "
            "도메인을 특정할 수 없다. 임의로 하나를 고르는 대신 종료한다."
        ),
    )


def _deferred(spec: DomainPackSpec) -> StopDecision:
    return StopDecision(
        stop=True,
        reason="unsupported_input",
        detail=(
            f"{spec.pack_id} 선택. one-input 실행 경로는 {spec.deferred_to}가 제공하며, "
            "다른 pack의 실험으로 대체하지 않는다."
        ),
    )


def select_domain_pack(
    *,
    manifest: AgentManifest,
    profile: BehaviorProfile,
    mission: Mission,
    card: AgentCard | None = None,
) -> PackSelection:
    """Pick the one pack this agent's evidence supports, or stop honestly.

    Returns a :class:`PackSelection` whose ``stop`` is ``None`` only when the
    chosen pack is executable by the one-input controller today. Callers must
    check it: a registered-but-deferred pack is a real selection with a real
    reason, and treating it as runnable is how a pack failure would get hidden
    behind another pack's success.
    """

    candidates = tuple(score_packs(manifest=manifest, profile=profile, mission=mission, card=card))

    if not candidates:
        return _stamp(
            PackSelection(
                candidates=candidates,
                why="등록된 어느 pack의 필수 도구 surface도 관찰되지 않았다.",
                expect="",
                fallback="대체 pack 없음.",
                stop=unroutable(manifest, profile),
            )
        )

    domain_candidates = [candidate for candidate in candidates if not candidate.is_fallback]
    if len(domain_candidates) >= 2 and domain_candidates[0].score == domain_candidates[1].score:
        tied = tuple(c for c in domain_candidates if c.score == domain_candidates[0].score)
        return _stamp(
            PackSelection(
                candidates=candidates,
                why="; ".join(candidate.why for candidate in tied),
                expect="",
                fallback="동점이 풀리기 전에는 어떤 pack도 실행하지 않는다.",
                stop=_ambiguous(tied),
            )
        )

    chosen = candidates[0]
    spec = chosen.spec
    selection = PackSelection(
        selected=chosen,
        candidates=candidates,
        why=chosen.why,
        expect=_expect_for(spec),
        evidence=_evidence_for(spec, manifest, profile),
        budget=spec.budget,
        fallback=_fallback_for(spec, candidates),
        stop=None if spec.executable else _deferred(spec),
    )
    return _stamp(selection)


def pack_failure_stop(selection: PackSelection, error_name: str) -> StopDecision:
    """A pack that raised is reported as that pack failing, nothing else.

    The rule from the issue is that a pack failure must not be hidden behind
    another pack's success, so this never re-routes -- it names the pack and the
    error class and stops.
    """

    pack_id = selection.pack_id or "unknown-pack"
    return StopDecision(
        stop=True,
        reason="unsupported_input",
        detail=(
            f"{pack_id} 실행이 {error_name}로 실패했다. 다른 pack의 결과로 대체하지 않으며, "
            "이 실행에서는 아무 실패도 측정되지 않았다."
        ),
    )


__all__ = [
    "ADHOC_PACK",
    "DOMAIN_PACKS",
    "LIFE_PACK",
    "PACKS_BY_ID",
    "PACKS_BY_KIND",
    "REGISTRY_VERSION",
    "RESEARCH_PACK",
    "ROUTABLE_TOOLS",
    "STOCK_PACK",
    "TICKET_PACK",
    "DomainKind",
    "DomainPackSpec",
    "PackBudget",
    "PackCandidate",
    "PackExecution",
    "PackSelection",
    "pack_failure_stop",
    "score_packs",
    "select_domain_pack",
    "selection_digest",
    "unroutable",
]
