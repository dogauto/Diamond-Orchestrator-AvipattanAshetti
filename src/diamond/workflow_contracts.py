from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
from typing import Any

from .domain import Provider
from .errors import ValidationError
from .evidence import digest_text
from .packets import _digest, _relative_path
from .validation import bounded_int, canonical_json, exact_keys, parse_canonical_json, task_id, text, utc_timestamp


class WorkflowStatus(str, Enum):
    CREATED = "CREATED"
    BUILDING = "BUILDING"
    DEBUGGING = "DEBUGGING"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    VERIFIED = "VERIFIED"
    EXHAUSTED = "EXHAUSTED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class TestCommand:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: int = 30
    output_limit: int = 65536

    def __post_init__(self) -> None:
        text(self.name, "test name", 128)
        if type(self.argv) is not tuple or not 1 <= len(self.argv) <= 64:
            raise ValidationError("test argv must be an immutable tuple of 1..64 arguments")
        for argument in self.argv:
            text(argument, "test argument", 16384)
        if not Path(self.argv[0]).is_absolute():
            raise ValidationError("test executable must be an absolute path")
        bounded_int(self.timeout_seconds, "test timeout", 1, 300)
        bounded_int(self.output_limit, "test output limit", 1, 1_048_576)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "argv": list(self.argv), "timeout_seconds": self.timeout_seconds,
                "output_limit": self.output_limit}

    @classmethod
    def from_dict(cls, value: object) -> "TestCommand":
        data = dict(exact_keys(value, {field.name for field in fields(cls)}, "TestCommand"))
        if type(data["argv"]) is not list:
            raise ValidationError("test argv must be a JSON array")
        data["argv"] = tuple(data["argv"])
        return cls(**data)


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_id: str
    objective: str
    workspace_root: str
    owned_paths: tuple[str, ...]
    tests: tuple[TestCommand, ...]
    builder_provider: Provider
    debugger_provider: Provider
    config_digest: str
    initial_manifest_json: str
    correction_round_limit: int = 2
    timeout_seconds: int = 180
    max_workers: int = 2
    schema_version: int = 1

    def __post_init__(self) -> None:
        task_id(self.workflow_id)
        text(self.objective, "workflow objective", 8192)
        text(self.workspace_root, "workflow workspace", 4096)
        if not Path(self.workspace_root).is_absolute() or ".." in Path(self.workspace_root).parts:
            raise ValidationError("workflow workspace must be an absolute path without traversal")
        if type(self.owned_paths) is not tuple or not 1 <= len(self.owned_paths) <= 32:
            raise ValidationError("workflow requires 1..32 immutable owned file paths")
        if len(set(self.owned_paths)) != len(self.owned_paths):
            raise ValidationError("workflow owned paths must be distinct")
        for value in self.owned_paths:
            _relative_path(value, "owned file")
            path = Path(value)
            if not path.parts or any(part.startswith(".") for part in path.parts) or str(path) != value:
                raise ValidationError("workflow owned files must be canonical, visible relative paths")
        if any(Path(first) in Path(second).parents for first in self.owned_paths for second in self.owned_paths):
            raise ValidationError("owned file paths must not overlap")
        if type(self.tests) is not tuple or not 1 <= len(self.tests) <= 32 or any(not isinstance(test, TestCommand) for test in self.tests):
            raise ValidationError("workflow requires 1..32 trusted test commands")
        if len({test.name for test in self.tests}) != len(self.tests):
            raise ValidationError("test names must be distinct")
        if len(canonical_json(list(self.owned_paths))) > 2048 or len(canonical_json([test.name for test in self.tests])) > 4096:
            raise ValidationError("workflow path/test names exceed the bounded context budget")
        if len(canonical_json({"objective": self.objective, "paths": list(self.owned_paths),
                              "tests": [test.name for test in self.tests]})) > 8192:
            raise ValidationError("workflow objective metadata exceeds the bounded context budget")
        if not isinstance(self.builder_provider, Provider) or not isinstance(self.debugger_provider, Provider):
            raise ValidationError("workflow routes must be Provider enum values")
        _digest(self.config_digest, "workflow config digest")
        if not isinstance(parse_canonical_json(self.initial_manifest_json), dict):
            raise ValidationError("initial workspace manifest must be a canonical object")
        bounded_int(self.correction_round_limit, "workflow correction limit", 0, 100)
        bounded_int(self.timeout_seconds, "workflow timeout", 1, 900)
        bounded_int(self.max_workers, "workflow max workers", 1, 64)
        bounded_int(self.schema_version, "workflow schema version", 1, 1)

    def to_dict(self) -> dict[str, Any]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result.update(owned_paths=list(self.owned_paths), tests=[test.to_dict() for test in self.tests],
                      builder_provider=self.builder_provider.value, debugger_provider=self.debugger_provider.value)
        return result

    @classmethod
    def from_dict(cls, value: object) -> "WorkflowSpec":
        data = dict(exact_keys(value, {field.name for field in fields(cls)}, "WorkflowSpec"))
        try:
            data["builder_provider"] = Provider(data["builder_provider"])
            data["debugger_provider"] = Provider(data["debugger_provider"])
            if type(data["owned_paths"]) is not list or type(data["tests"]) is not list:
                raise ValidationError("workflow paths/tests must be arrays")
            data["owned_paths"] = tuple(data["owned_paths"])
            data["tests"] = tuple(TestCommand.from_dict(item) for item in data["tests"])
            return cls(**data)
        except (ValueError, TypeError) as exc:
            raise ValidationError("invalid workflow specification") from exc


@dataclass(frozen=True)
class FailurePacket:
    workflow_id: str
    attempt_id: str
    round_number: int
    candidate_digest: str
    report_json: str
    created_at: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        task_id(self.workflow_id)
        task_id(self.attempt_id)
        bounded_int(self.round_number, "failure round", 0, 100)
        _digest(self.candidate_digest, "failure candidate")
        if not isinstance(parse_canonical_json(self.report_json), dict):
            raise ValidationError("failure report must be a canonical object")
        utc_timestamp(self.created_at)
        bounded_int(self.schema_version, "failure packet schema version", 1, 1)

    def to_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    def digest(self) -> str:
        return digest_text(canonical_json(self.to_dict()))

    @classmethod
    def from_dict(cls, value: object) -> "FailurePacket":
        return cls(**dict(exact_keys(value, {field.name for field in fields(cls)}, "FailurePacket")))


@dataclass(frozen=True)
class WorkflowSnapshot:
    spec: WorkflowSpec
    status: WorkflowStatus
    revision: int
    corrections_used: int
    manifest_json: str
    active_task_id: str | None
    active_attempt_id: str | None
    failure: FailurePacket | None
    updated_at: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.spec, WorkflowSpec) or not isinstance(self.status, WorkflowStatus):
            raise ValidationError("workflow snapshot requires validated spec and status")
        bounded_int(self.revision, "workflow revision", 1, 2**63 - 1)
        bounded_int(self.corrections_used, "corrections used", 0, self.spec.correction_round_limit)
        if not isinstance(parse_canonical_json(self.manifest_json), dict):
            raise ValidationError("workflow manifest must be a canonical object")
        for identifier in (self.active_task_id, self.active_attempt_id):
            if identifier is not None:
                task_id(identifier)
        if self.status in {WorkflowStatus.BUILDING, WorkflowStatus.DEBUGGING} and (self.active_task_id is None or self.active_attempt_id is None):
            raise ValidationError("active workflow requires task and attempt identity")
        if self.failure is not None and (not isinstance(self.failure, FailurePacket) or self.failure.workflow_id != self.spec.workflow_id):
            raise ValidationError("failure packet does not belong to workflow")
        if self.status is WorkflowStatus.NEEDS_CORRECTION and self.failure is None:
            raise ValidationError("correction requires a persisted failure packet")
        utc_timestamp(self.updated_at)
        if not isinstance(self.detail, str) or len(self.detail) > 4096:
            raise ValidationError("workflow detail must be bounded text")

    def to_dict(self) -> dict[str, Any]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result.update(spec=self.spec.to_dict(), status=self.status.value,
                      failure=self.failure.to_dict() if self.failure else None)
        return result

    @classmethod
    def from_dict(cls, value: object) -> "WorkflowSnapshot":
        data = dict(exact_keys(value, {field.name for field in fields(cls)}, "WorkflowSnapshot"))
        try:
            data["spec"] = WorkflowSpec.from_dict(data["spec"])
            data["status"] = WorkflowStatus(data["status"])
            data["failure"] = FailurePacket.from_dict(data["failure"]) if data["failure"] is not None else None
            return cls(**data)
        except (TypeError, ValueError) as exc:
            raise ValidationError("invalid workflow snapshot") from exc
