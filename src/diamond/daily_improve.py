"""Evidence-backed opportunity discovery for bounded daily improvement.

This module is deliberately read-only. It turns controller evidence into
ranked candidates; it does not select files, weaken tests, launch providers,
or publish source changes.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .store import EventStore
from .evidence import digest_text
from .validation import canonical_json
from .validation import bounded_int, utc_now


_DIGEST = re.compile(r"\b[0-9a-f]{32,64}\b")
_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\b")
_DURATION = re.compile(r"\b\d+(?:\.\d+)?\s*(?:s|seconds)\b")


@dataclass(frozen=True)
class EvidenceInput:
    """The bounded, persisted fields used by the deterministic analyzer."""

    evidence_id: str
    attempt_id: str
    kind: str
    created_at: str
    provider: str
    role: str
    payload: dict[str, Any]
    workspace: str = "UNKNOWN"
    lineage: str | None = None
    source_digest: str | None = None
    cli_version: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class FailureObservation:
    category: str
    severity: str
    summary: str
    evidence_id: str
    attempt_id: str
    provider: str
    role: str
    created_at: str
    test_name: str | None = None
    relative_path: str | None = None
    candidate_digest: str | None = None
    workspace: str = "UNKNOWN"
    lineage: str | None = None


@dataclass
class _CandidateGroup:
    category: str
    severity: str
    signature: str
    summaries: list[str] = field(default_factory=list)
    observations: list[FailureObservation] = field(default_factory=list)

    def add(self, observation: FailureObservation) -> None:
        if observation in self.observations:
            return
        self.observations.append(observation)
        if observation.summary not in self.summaries:
            self.summaries.append(observation.summary)


@dataclass(frozen=True)
class ImprovementCandidate:
    signature: str
    category: str
    severity: str
    score: int
    evidence_support: str
    occurrences: int
    evidence_count: int
    objective: str
    recommendation: str
    providers: tuple[str, ...]
    roles: tuple[str, ...]
    tests: tuple[str, ...]
    paths: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    first_seen: str
    last_seen: str
    known_task_lineages: int
    workspace: str
    adjudication: str
    resolution_evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "category": self.category,
            "severity": self.severity,
            "score": self.score,
            "evidence_support": self.evidence_support,
            "known_task_lineages": self.known_task_lineages,
            "workspace": self.workspace,
            "adjudication": self.adjudication,
            "resolution_evidence_ids": list(self.resolution_evidence_ids),
            "occurrences": self.occurrences,
            "evidence_count": self.evidence_count,
            "objective": self.objective,
            "recommendation": self.recommendation,
            "providers": list(self.providers),
            "roles": list(self.roles),
            "tests": list(self.tests),
            "paths": list(self.paths),
            "evidence_ids": list(self.evidence_ids),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass(frozen=True)
class ImprovementAnalysis:
    schema_version: int
    generated_at: str
    window_hours: int
    evidence_records: int
    failure_observations: int
    candidates: tuple[ImprovementCandidate, ...]
    historical_candidates: tuple[ImprovementCandidate, ...] = ()
    read_window: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "window_hours": self.window_hours,
            "evidence_records": self.evidence_records,
            "failure_observations": self.failure_observations,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "historical_candidates": [candidate.to_dict() for candidate in self.historical_candidates],
            "read_window": self.read_window,
            "outcome": "review_candidates" if self.candidates else "no_actionable_evidence",
            "limitations": ["Scores are heuristic prioritization, not calibrated confidence or proof of a defect.",
                            "Known task lineages count recorded workflow families, not independent statistical samples.",
                            "Later passing tests are observations, not proof against delayed regressions."],
            "execution": {
                "provider_launch": False,
                "source_publication": False,
                "requires_review": True,
            },
        }


def _normalize(value: str) -> str:
    value = _DIGEST.sub("<digest>", value)
    value = _TIMESTAMP.sub("<timestamp>", value)
    value = _DURATION.sub("<duration>", value)
    return " ".join(value.split())[:768]


def _failure_tests(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    report: object = payload
    failure = payload.get("failure_packet")
    if isinstance(failure, dict) and isinstance(failure.get("report_json"), str):
        try:
            report = json.loads(failure["report_json"])
        except (TypeError, ValueError):
            report = payload
    if not isinstance(report, dict) or not isinstance(report.get("tests"), list):
        return ()
    return (item for item in report["tests"] if isinstance(item, dict) and item.get("passed") is False)


def _observations(item: EvidenceInput) -> tuple[FailureObservation, ...]:
    payload = item.payload
    observations: list[FailureObservation] = []
    if item.kind in {"required_tests", "workflow_verification"}:
        for test in _failure_tests(payload):
            name = str(test.get("name") or "unnamed test")
            detail = str(test.get("stderr") or test.get("stdout") or "test exited unsuccessfully")
            observations.append(FailureObservation(
                "test_failure", "high", f"{name}: {_normalize(detail)}", item.evidence_id,
                item.attempt_id, item.provider, item.role, item.created_at, name,
                None, payload.get("candidate_digest")))
        if payload.get("passed") is False and not observations:
            observations.append(FailureObservation(
                "verification_failure", "high", "workflow verification failed without a structured test failure",
                item.evidence_id, item.attempt_id, item.provider, item.role, item.created_at,
                candidate_digest=payload.get("candidate_digest")))
    elif item.kind == "delivery_gate" and payload.get("passed") is False:
        findings = payload.get("findings")
        if isinstance(findings, list):
            for finding in findings:
                if not isinstance(finding, dict) or finding.get("severity") != "blocking":
                    continue
                description = str(finding.get("description") or "blocking delivery finding")
                observations.append(FailureObservation(
                    "delivery_finding", "medium", _normalize(description), item.evidence_id,
                    item.attempt_id, item.provider, item.role, item.created_at,
                    relative_path=finding.get("relative_path"), candidate_digest=payload.get("candidate_digest")))
        if not observations:
            observations.append(FailureObservation(
                "delivery_failure", "critical", "delivery stage rejected the candidate without a blocking finding",
                item.evidence_id, item.attempt_id, item.provider, item.role, item.created_at,
                candidate_digest=payload.get("candidate_digest")))
    elif item.kind == "provider_result":
        execution_status = payload.get("execution_status")
        result_status = payload.get("result_status")
        if execution_status not in {None, "succeeded"} or result_status not in {None, "valid"}:
            detail = payload.get("detail") or payload.get("stderr") or result_status or execution_status
            observations.append(FailureObservation(
                "provider_failure", "high", _normalize(str(detail or "provider execution failed")),
                item.evidence_id, item.attempt_id, item.provider, item.role, item.created_at))
    from dataclasses import replace
    return tuple(replace(observation, workspace=item.workspace, lineage=item.lineage) for observation in observations)


def _candidate(group: _CandidateGroup, passing: dict) -> ImprovementCandidate:
    observations = group.observations
    attempts = {item.attempt_id for item in observations}
    lineages = {item.lineage for item in observations if item.lineage is not None}
    score = {"critical": 35, "high": 25, "medium": 15}.get(group.severity, 10)
    score += min(30, len(lineages) * 10)
    support = "unknown_lineage" if not lineages else "single_lineage" if len(lineages) == 1 else "multiple_lineages"
    tests = tuple(sorted({item.test_name for item in observations if item.test_name}))
    paths = tuple(sorted({item.relative_path for item in observations if item.relative_path}))
    providers = tuple(sorted({item.provider for item in observations}))
    roles = tuple(sorted({item.role for item in observations}))
    first_seen = min(item.created_at for item in observations)
    last_seen = max(item.created_at for item in observations)
    subject = tests[0] if tests else paths[0] if paths else group.category
    objective = f"Investigate and reproduce the recurring {group.category.replace('_', ' ')} involving {subject}"
    recommendation = ("Reproduce the failure against the exact candidate before changing source; define a regression "
                      "test and compare the baseline with the proposed fix."
                      if group.category != "delivery_finding" else
                       "Reconcile candidate identity and evidence before any source change; rerun independent delivery gates.")
    if group.category == "provider_failure":
        recommendation = "Investigate provider availability, authentication, quota and execution evidence before proposing a source change."
    resolutions = []
    unresolved = False
    for item in observations:
        later = passing.get((item.workspace, item.lineage, item.test_name)) if item.lineage and item.test_name else None
        if later and later.created_at > item.created_at:
            resolutions.append(later.evidence_id)
        else:
            unresolved = True
    adjudication = "unadjudicated_claim" if group.category == "delivery_finding" else "observed_failure_requires_reproduction"
    if not unresolved:
        adjudication = "later_pass_observed"
    return ImprovementCandidate(
        group.signature, group.category, group.severity, min(100, score), support,
        len(attempts), len({item.evidence_id for item in observations}), objective,
        recommendation, providers, roles, tests, paths,
        tuple(sorted({item.evidence_id for item in observations})), first_seen, last_seen,
        len(lineages), observations[0].workspace, adjudication, tuple(sorted(set(resolutions))))


def analyze_evidence(records: Iterable[EvidenceInput], window_hours: int = 168,
                    max_candidates: int = 5) -> ImprovementAnalysis:
    bounded_int(window_hours, "daily improvement window hours", 1, 24 * 365)
    bounded_int(max_candidates, "daily improvement candidate limit", 1, 100)
    groups: dict[str, _CandidateGroup] = {}
    evidence_records = 0
    failure_count = 0
    passing = {}
    for record in records:
        evidence_records += 1
        if (record.kind == "required_tests" and record.payload.get("passed") is True
                and isinstance(record.payload.get("tests"), list)):
            for test in record.payload.get("tests", []):
                if isinstance(test, dict) and test.get("passed") is True and isinstance(test.get("name"), str):
                    key = (record.workspace, record.lineage, test["name"])
                    if key not in passing or passing[key].created_at < record.created_at:
                        passing[key] = record
        for observation in _observations(record):
            failure_count += 1
            signature = digest_text(canonical_json([observation.workspace, observation.category,
                                   observation.test_name, observation.relative_path, observation.summary]))
            group = groups.setdefault(signature, _CandidateGroup(
                observation.category, observation.severity, signature))
            group.add(observation)
    candidates = sorted((_candidate(group, passing) for group in groups.values()),
                         key=lambda item: (-item.score, -item.occurrences, item.signature))
    active = [item for item in candidates if item.adjudication != "later_pass_observed"]
    historical = [item for item in candidates if item.adjudication == "later_pass_observed"]
    return ImprovementAnalysis(2, utc_now(), window_hours, evidence_records, failure_count,
                                tuple(active[:max_candidates]), tuple(historical[:max_candidates]))


def analyze_ledger(ledger: EventStore, window_hours: int = 168,
                   max_candidates: int = 5, *, project: Path | None = None,
                   max_records: int = 1000, max_bytes: int = 8 * 1024 * 1024) -> ImprovementAnalysis:
    from .improvement_evidence import analyze_snapshot
    return analyze_snapshot(ledger, window_hours, max_candidates, project=project,
                            max_records=max_records, max_bytes=max_bytes)
