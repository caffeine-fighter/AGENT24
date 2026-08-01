"""Classify an agent's tools and find the paths that make attacks worth trying.

Phase 0 provides the signatures plus a minimal keyword table so the contract and
the fakes are usable end to end.  Issue #3 extends the table and the risk-path
heuristics; the function signatures and return types are frozen.
"""

from collections.abc import Iterable, Sequence

from .models import CapabilityCategory, ClassifiedCapability, RiskPath, ToolSpec

_UNTRUSTED_PREFIXES = ("web.", "search.", "browser.")
_DATA_PREFIXES = ("file.", "db.", "doc.")
_PRIVILEGED_NAMES = ("payment.charge", "payment.refund", "email.send")
_SIDE_EFFECT_PREFIXES = ("calendar.", "order.", "payment.", "email.")

_READ_ONLY_NAMES = ("payment.status",)

_TRUST_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("web.", "web_page"),
    ("search.", "web_page"),
    ("browser.", "web_page"),
    ("email.read", "email_body"),
    ("inbox.", "email_body"),
    ("file.", "document"),
    ("doc.", "document"),
)


def _trust_for(name: str) -> str:
    for prefix, label in _TRUST_BY_PREFIX:
        if name.startswith(prefix):
            return label
    return "tool_output"


def _categories_for(spec: ToolSpec) -> frozenset[CapabilityCategory]:
    if spec.category_hint is not None:
        return frozenset({spec.category_hint})

    name = spec.name
    categories: set[CapabilityCategory] = set()

    if name.startswith(_UNTRUSTED_PREFIXES):
        categories.add(CapabilityCategory.UNTRUSTED_SOURCE)
    if name.startswith(_DATA_PREFIXES):
        categories.add(CapabilityCategory.DATA_ACCESS)
    if name in _PRIVILEGED_NAMES:
        categories.add(CapabilityCategory.PRIVILEGED_SINK)
        categories.add(CapabilityCategory.SIDE_EFFECT)
    elif name.startswith(_SIDE_EFFECT_PREFIXES) and name not in _READ_ONLY_NAMES:
        categories.add(CapabilityCategory.SIDE_EFFECT)

    # The card's own declaration wins over the name when it claims more.
    if spec.side_effect:
        categories.add(CapabilityCategory.SIDE_EFFECT)
    if spec.irreversible:
        categories.add(CapabilityCategory.PRIVILEGED_SINK)

    return frozenset(categories)


def classify(tools: Iterable[ToolSpec]) -> list[ClassifiedCapability]:
    """Label every tool with what it can reach and how much its output is trusted."""

    return [
        ClassifiedCapability(
            tool=spec.name,
            categories=_categories_for(spec),
            trust=_trust_for(spec.name),  # type: ignore[arg-type]
        )
        for spec in tools
    ]


def find_risk_paths(caps: Sequence[ClassifiedCapability]) -> list[RiskPath]:
    """Enumerate untrusted-source -> (data-access) -> privileged-sink chains.

    Each chain is a concrete reason to raise the priority of an injection or
    exfiltration experiment: content the agent does not control can influence a
    call the user would never authorise.
    """

    sources = [cap for cap in caps if CapabilityCategory.UNTRUSTED_SOURCE in cap.categories]
    accesses = [cap for cap in caps if CapabilityCategory.DATA_ACCESS in cap.categories]
    sinks = [cap for cap in caps if CapabilityCategory.PRIVILEGED_SINK in cap.categories]

    paths: list[RiskPath] = []
    for source in sources:
        for sink in sinks:
            paths.append(
                RiskPath(
                    source_tool=source.tool,
                    via=(),
                    sink_tool=sink.tool,
                    note=f"{source.tool} content can influence {sink.tool} directly",
                )
            )
            for access in accesses:
                if access.tool in (source.tool, sink.tool):
                    continue
                paths.append(
                    RiskPath(
                        source_tool=source.tool,
                        via=(access.tool,),
                        sink_tool=sink.tool,
                        note=f"{source.tool} can steer {access.tool} data into {sink.tool}",
                    )
                )
    return paths


__all__ = ["classify", "find_risk_paths"]
