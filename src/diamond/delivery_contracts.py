from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from typing import Any

from .domain import Provider, Role
from .errors import ExecutionGuardError, ValidationError
from .integration import _components, _manifest
from .packets import _digest
from .validation import bounded_int, canonical_json, command_id, exact_keys, parse_canonical_json, task_id, text, utc_timestamp
from .workflow_contracts import TestCommand, WorkflowSpec


ROLES = (Role.INTEGRATOR, Role.QA, Role.REVIEWER, Role.RELEASE)


class DeliveryStatus(str, Enum):
    CREATED = "CREATED"
    INTEGRATING = "INTEGRATING"
    INTEGRATED = "INTEGRATED"
    QA_RUNNING = "QA_RUNNING"
    QA_PASSED = "QA_PASSED"
    REVIEW_RUNNING = "REVIEW_RUNNING"
    REVIEW_PASSED = "REVIEW_PASSED"
    RELEASE_RUNNING = "RELEASE_RUNNING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


READY = (DeliveryStatus.CREATED, DeliveryStatus.INTEGRATED, DeliveryStatus.QA_PASSED, DeliveryStatus.REVIEW_PASSED)
RUNNING = (DeliveryStatus.INTEGRATING, DeliveryStatus.QA_RUNNING, DeliveryStatus.REVIEW_RUNNING, DeliveryStatus.RELEASE_RUNNING)
PASSED = (DeliveryStatus.INTEGRATED, DeliveryStatus.QA_PASSED, DeliveryStatus.REVIEW_PASSED, DeliveryStatus.ACCEPTED)


def next_role(status: DeliveryStatus) -> Role:
    if status not in READY:
        raise ExecutionGuardError("delivery has no ready stage; inspect retained evidence")
    return ROLES[READY.index(status)]


def requirements(value: str, tests: tuple[TestCommand, ...]) -> dict[str, Any]:
    data = parse_canonical_json(value)
    if type(data) is not dict or not 1 <= len(data) <= 16 or len(value) > 8192:
        raise ValidationError("delivery requires 1..16 bounded acceptance requirements")
    known = {test.name for test in tests}
    for identifier, item in data.items():
        command_id(identifier)
        exact_keys(item, {"description", "tests"}, "requirement")
        text(item["description"], "requirement description", 2048)
        if type(item["tests"]) is not list or not item["tests"] or any(type(name) is not str for name in item["tests"]):
            raise ValidationError("requirement must cite trusted test names")
        if len(set(item["tests"])) != len(item["tests"]) or not set(item["tests"]) <= known:
            raise ValidationError("requirement test names are duplicate or untrusted")
    if {name for item in data.values() for name in item["tests"]} != known:
        raise ValidationError("all required tests must belong to acceptance requirements")
    return data


@dataclass(frozen=True)
class DeliverySpec:
    delivery_id: str
    objective: str
    workspace_root: str
    base_manifest_json: str
    components_json: str
    tests: tuple[TestCommand, ...]
    requirements_json: str
    routes: tuple[Provider, ...]
    config_digest: str
    supersedes: str | None = None
    timeout_seconds: int = 180
    max_workers: int = 2
    schema_version: int = 1

    def __post_init__(self) -> None:
        task_id(self.delivery_id)
        if self.supersedes is not None:
            task_id(self.supersedes)
            if self.supersedes == self.delivery_id:
                raise ValidationError("delivery cannot supersede itself")
        _manifest(self.base_manifest_json)
        _components(self.components_json, self.base_manifest_json)
        if type(self.routes) is not tuple or len(self.routes) != 4 or any(not isinstance(p, Provider) for p in self.routes):
            raise ValidationError("delivery requires four immutable explicit provider routes")
        _digest(self.config_digest, "delivery configuration")
        bounded_int(self.schema_version, "delivery schema", 1, 1)
        self.work_spec()
        requirements(self.requirements_json, self.tests)
        if len(canonical_json({"objective": self.objective, "requirements": requirements(self.requirements_json, self.tests)})) > 12288:
            raise ValidationError("delivery acceptance metadata exceeds context budget")

    def work_spec(self) -> WorkflowSpec:
        paths = tuple(sorted({change["relative_path"] for component in parse_canonical_json(self.components_json)
                              for change in component["changes"]}))
        return WorkflowSpec(self.delivery_id, self.objective, self.workspace_root, paths, self.tests,
                            self.routes[0], self.routes[2], self.config_digest, self.base_manifest_json,
                            correction_round_limit=0, timeout_seconds=self.timeout_seconds, max_workers=self.max_workers)

    def provider(self, role: Role) -> Provider:
        if role not in ROLES:
            raise ValidationError("role is not a delivery stage")
        return self.routes[ROLES.index(role)]

    def to_dict(self) -> dict[str, Any]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result.update(tests=[test.to_dict() for test in self.tests], routes=[provider.value for provider in self.routes])
        return result

    @classmethod
    def from_dict(cls, value: object) -> "DeliverySpec":
        data = dict(exact_keys(value, {field.name for field in fields(cls)}, "DeliverySpec"))
        try:
            if type(data["tests"]) is not list or type(data["routes"]) is not list:
                raise ValidationError("delivery tests/routes must be arrays")
            data["tests"] = tuple(TestCommand.from_dict(test) for test in data["tests"])
            data["routes"] = tuple(Provider(provider) for provider in data["routes"])
            return cls(**data)
        except (ValueError, TypeError) as exc:
            raise ValidationError("invalid delivery specification") from exc


@dataclass(frozen=True)
class DeliverySnapshot:
    spec: DeliverySpec
    status: DeliveryStatus
    revision: int
    manifest_json: str
    base_sources_json: str
    test_inputs_json: str
    reports_json: str
    inherited_findings_json: str
    prior_manifest_json: str | None
    decisions_json: str
    active_task_id: str | None
    active_attempt_id: str | None
    updated_at: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.spec, DeliverySpec) or not isinstance(self.status, DeliveryStatus):
            raise ValidationError("delivery snapshot requires validated specification and status")
        bounded_int(self.revision, "delivery revision", 1, 2**63 - 1)
        _manifest(self.manifest_json)
        for value in (self.base_sources_json, self.test_inputs_json, self.reports_json, self.inherited_findings_json, self.decisions_json):
            parse_canonical_json(value)
        if len(self.base_sources_json) > 16384 or type(parse_canonical_json(self.base_sources_json)) is not list:
            raise ValidationError("base source context must be a bounded array")
        if len(self.test_inputs_json) > 16384 or type(parse_canonical_json(self.test_inputs_json)) is not dict:
            raise ValidationError("trusted input context must be a bounded object")
        if type(parse_canonical_json(self.reports_json)) is not dict or type(parse_canonical_json(self.inherited_findings_json)) is not list:
            raise ValidationError("delivery reports/findings have invalid shapes")
        if self.prior_manifest_json is not None:
            _manifest(self.prior_manifest_json)
        for identifier in (self.active_task_id, self.active_attempt_id):
            if identifier is not None:
                task_id(identifier)
        if self.status in RUNNING and (self.active_task_id is None or self.active_attempt_id is None):
            raise ValidationError("active delivery stage requires task and attempt identities")
        utc_timestamp(self.updated_at)
        if type(self.detail) is not str or len(self.detail) > 4096:
            raise ValidationError("delivery detail must be bounded text")

    def to_dict(self) -> dict[str, Any]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result.update(spec=self.spec.to_dict(), status=self.status.value)
        return result

    @classmethod
    def from_dict(cls, value: object) -> "DeliverySnapshot":
        data = dict(exact_keys(value, {field.name for field in fields(cls)}, "DeliverySnapshot"))
        try:
            data["spec"] = DeliverySpec.from_dict(data["spec"])
            data["status"] = DeliveryStatus(data["status"])
            return cls(**data)
        except (ValueError, TypeError) as exc:
            raise ValidationError("invalid delivery snapshot") from exc
