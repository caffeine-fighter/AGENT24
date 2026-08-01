"""Shared, byte-stable diagnosis records for deterministic domain gyms."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any


def canonical_json(value: Any) -> str:
    """Return canonical JSON and reject values that cannot be replayed."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A stable pointer into one synthetic fixture resource."""

    kind: str
    resource_id: str
    field: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "resource_id": self.resource_id,
            "field": self.field,
        }


@dataclass(frozen=True, slots=True)
class DomainFinding:
    """One measured domain failure; never an uncited model opinion."""

    finding_id: str
    fault_id: str
    category: str
    severity: str
    observed: Any
    expected: Any
    evidence_refs: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if not self.evidence_refs:
            raise ValueError("a domain finding must contain at least one evidence reference")
        if self.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"unsupported severity: {self.severity}")
        # Fail at construction time instead of discovering non-JSON evidence
        # while rendering a judge-facing report.
        canonical_json(self.observed)
        canonical_json(self.expected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "fault_id": self.fault_id,
            "category": self.category,
            "severity": self.severity,
            "observed": copy.deepcopy(self.observed),
            "expected": copy.deepcopy(self.expected),
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
        }


@dataclass(frozen=True, slots=True)
class DiagnosisReport:
    """Controller-only result produced from an AUT assessment."""

    pack_id: str
    fixture_id: str
    seed: int
    fixture_digest: str
    findings: tuple[DomainFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    def finding_ids(self) -> tuple[str, ...]:
        return tuple(finding.finding_id for finding in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "fixture_id": self.fixture_id,
            "seed": self.seed,
            "fixture_digest": self.fixture_digest,
            "passed": self.passed,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @property
    def report_digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


__all__ = [
    "DiagnosisReport",
    "DomainFinding",
    "EvidenceRef",
    "canonical_json",
]
