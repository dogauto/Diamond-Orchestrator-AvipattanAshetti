from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

from .errors import ExecutionGuardError, TransitionError, ValidationError
from .validation import (bounded_int, command_id, exact_keys, parse_canonical_json,
                         task_id, text, utc_timestamp)


class Role(str, Enum):
    PLANNER = "planner"
    RESEARCHER = "researcher"
    ARCHITECT = "architect"
    BUILDER = "builder"
    INTEGRATOR = "integrator"
    DEBUGGER = "debugger"
    QA = "qa"
    REVIEWER = "reviewer"
    RELEASE = "release"


class Provider(str, Enum):
    GEMINI = "gemini"
    ANTIGRAVITY = "antigravity"
    CODEX = "codex"
    OMNIRUSH = "omnirush"
    COPILOT = "copilot"
    CLAUDE = "claude"


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    REVIEWING = "REVIEWING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


FOUNDATION_STATES = frozenset({TaskStatus.CREATED, TaskStatus.BLOCKED, TaskStatus.CANCELLED})


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    objective: str
    role: Role
    provider: Provider
    schema_version: int = 1
    acceptance_conditions: tuple[str, ...] | None = None
    dependencies: tuple[str, ...] | None = None
    owned_paths: tuple[str, ...] | None = None
    allowed_tools: tuple[str, ...] | None = None
    allowed_skills: tuple[str, ...] | None = None
    required_tests: tuple[str, ...] | None = None
    timeout_seconds: int | None = None
    correction_round_limit: int | None = None

    def __post_init__(self) -> None:
        task_id(self.task_id)
        text(self.objective, "objective")
        bounded_int(self.schema_version, "task schema_version", 1, 1)
        if not isinstance(self.role, Role) or not isinstance(self.provider, Provider):
            raise ValidationError("role/provider must be Role/Provider enum values")
        for name in ("acceptance_conditions", "dependencies", "owned_paths", "allowed_tools", "allowed_skills", "required_tests"):
            values = getattr(self, name)
            if values is not None:
                if type(values) is not tuple or len(values) > 256:
                    raise ValidationError(f"{name} must be an immutable tuple of at most 256 strings or None")
                for value in values:
                    task_id(value) if name == "dependencies" else text(value, name, 4096)
        if self.timeout_seconds is not None:
            bounded_int(self.timeout_seconds, "timeout_seconds", 1, 86_400)
        if self.correction_round_limit is not None:
            bounded_int(self.correction_round_limit, "correction_round_limit", 0, 100)

    def to_dict(self) -> dict[str, Any]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result.update(role=self.role.value, provider=self.provider.value)
        return {key: list(value) if type(value) is tuple else value for key, value in result.items()}

    @classmethod
    def from_dict(cls, value: object) -> "TaskSpec":
        data = dict(exact_keys(value, {field.name for field in fields(cls)}, "TaskSpec"))
        try:
            data["role"] = Role(data["role"])
            data["provider"] = Provider(data["provider"])
            for name in ("acceptance_conditions", "dependencies", "owned_paths", "allowed_tools", "allowed_skills", "required_tests"):
                if data[name] is not None:
                    if type(data[name]) is not list:
                        raise ValidationError(f"{name} must be a JSON array or null")
                    data[name] = tuple(data[name])
            return cls(**data)
        except (ValueError, TypeError) as exc:
            raise ValidationError("invalid TaskSpec enum or field type") from exc


@dataclass(frozen=True)
class TaskSnapshot:
    spec: TaskSpec
    status: TaskStatus
    revision: int
    created_at: str
    updated_at: str
    blocking_reason: str | None = None
    resume_status: TaskStatus | None = None
    plan_revision: int | None = None
    plan_digest: str | None = None
    current_attempt: str | None = None
    correction_rounds_used: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.spec, TaskSpec):
            raise ValidationError("snapshot spec must be a TaskSpec")
        if not isinstance(self.status, TaskStatus) or self.status not in FOUNDATION_STATES:
            raise ExecutionGuardError("execution snapshots require mechanisms unavailable in the foundation")
        bounded_int(self.revision, "revision", 1, 2**63 - 1)
        utc_timestamp(self.created_at)
        utc_timestamp(self.updated_at)
        if self.status is TaskStatus.BLOCKED:
            text(self.blocking_reason, "blocking reason")
            if self.resume_status is not TaskStatus.CREATED:
                raise ValidationError("foundation blocked tasks must resume CREATED")
        elif self.blocking_reason is not None or self.resume_status is not None:
            raise ValidationError("only BLOCKED tasks carry blocking reason/resume state")
        if any(value is not None for value in (self.plan_revision, self.plan_digest, self.current_attempt)):
            raise ExecutionGuardError("accepted packets and attempts are unavailable in the foundation")
        bounded_int(self.correction_rounds_used, "correction_rounds_used", 0, 0)

    def to_dict(self) -> dict[str, Any]:
        return {"spec": self.spec.to_dict(), "status": self.status.value, "revision": self.revision,
                "created_at": self.created_at, "updated_at": self.updated_at,
                "blocking_reason": self.blocking_reason,
                "resume_status": self.resume_status.value if self.resume_status else None,
                "plan_revision": None, "plan_digest": None, "current_attempt": None,
                "correction_rounds_used": 0}

    @classmethod
    def from_dict(cls, value: object) -> "TaskSnapshot":
        data = dict(exact_keys(value, {field.name for field in fields(cls)}, "TaskSnapshot"))
        try:
            data["spec"] = TaskSpec.from_dict(data["spec"])
            data["status"] = TaskStatus(data["status"])
            if data["resume_status"] is not None:
                data["resume_status"] = TaskStatus(data["resume_status"])
            return cls(**data)
        except (ValueError, TypeError) as exc:
            raise ValidationError("invalid TaskSnapshot enum or field type") from exc


@dataclass(frozen=True)
class LedgerEvent:
    event_id: str
    task_id: str
    revision: int
    event_type: str
    timestamp: str
    command_id: str
    payload_json: str
    schema_version: int = 1
    actor: str = "controller"

    def __post_init__(self) -> None:
        task_id(self.event_id)
        task_id(self.task_id)
        bounded_int(self.revision, "event revision", 1, 2**63 - 1)
        bounded_int(self.schema_version, "event schema_version", 1, 1)
        if not isinstance(self.event_type, str) or self.event_type not in {"TASK_CREATED", "TASK_TRANSITIONED"} or self.actor != "controller":
            raise ValidationError("unsupported event type or actor")
        utc_timestamp(self.timestamp)
        command_id(self.command_id)
        parse_canonical_json(self.payload_json)

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "schema_version": self.schema_version,
                "task_id": self.task_id, "revision": self.revision, "event_type": self.event_type,
                "timestamp": self.timestamp, "actor": self.actor, "command_id": self.command_id,
                "payload": parse_canonical_json(self.payload_json)}


def validate_transition(current: TaskSnapshot, target: TaskStatus, reason: str | None) -> None:
    if not isinstance(current, TaskSnapshot):
        raise ValidationError("current must be a TaskSnapshot")
    if not isinstance(target, TaskStatus):
        raise ValidationError("target must be a TaskStatus enum")
    if target not in FOUNDATION_STATES:
        raise ExecutionGuardError("execution states require accepted packets, dispatch claims, and evidence; unavailable")
    allowed = {TaskStatus.CREATED: {TaskStatus.BLOCKED, TaskStatus.CANCELLED},
               TaskStatus.BLOCKED: {TaskStatus.CREATED, TaskStatus.CANCELLED},
               TaskStatus.CANCELLED: set()}
    if target not in allowed[current.status]:
        raise TransitionError(f"cannot transition {current.status.value} to {target.value}")
    if target is TaskStatus.BLOCKED or current.status is TaskStatus.BLOCKED and target is TaskStatus.CREATED:
        text(reason, "blocking/resolving reason")
    elif reason is not None:
        text(reason, "transition reason")
