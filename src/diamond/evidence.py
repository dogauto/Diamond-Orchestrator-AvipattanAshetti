import hashlib
import os
import re
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any

from .adapters import Invocation
from .domain import Provider, Role
from .errors import ValidationError
from .packets import ArtifactRef
from .results import NormalizedResult
from .supervisor import ExecutionResult
from .validation import (bounded_int, canonical_json, exact_keys, parse_canonical_json,
                         task_id, text, utc_timestamp)


_DIGEST = re.compile(r"[0-9a-f]{64}")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"),
    re.compile(r"(?i)((?:[\"']?(?:api[_-]?key|token|secret|password)[\"']?)\s*[:=]\s*[\"']?)[^,\s}\"']+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s\"']+"),
    re.compile(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}\b"),
)


class AttemptStatus(str, Enum):
    INTENDED = "INTENDED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    AMBIGUOUS = "AMBIGUOUS"
    CANCELLED = "CANCELLED"


def validate_attempt_update(previous: "ExecutionAttempt", target: "ExecutionAttempt") -> None:
    allowed = {
        AttemptStatus.INTENDED: {AttemptStatus.STARTED, AttemptStatus.AMBIGUOUS, AttemptStatus.CANCELLED},
        AttemptStatus.STARTED: {AttemptStatus.SUCCEEDED, AttemptStatus.FAILED,
                                AttemptStatus.AMBIGUOUS, AttemptStatus.CANCELLED},
        AttemptStatus.AMBIGUOUS: {AttemptStatus.SUCCEEDED, AttemptStatus.FAILED,
                                AttemptStatus.CANCELLED, AttemptStatus.AMBIGUOUS},
        AttemptStatus.SUCCEEDED: set(),
        AttemptStatus.FAILED: set(),
        AttemptStatus.CANCELLED: set(),
    }
    if target.status not in allowed[previous.status]:
        raise ValidationError(f"cannot update attempt {previous.status.value} to {target.status.value}")
    if any(getattr(previous, name) != getattr(target, name)
           for name in ("attempt_id", "schema_version", "task_id", "task_revision", "role", "provider", "executable",
                        "workspace_root", "requested_model", "cli_version", "created_at")):
        raise ValidationError("attempt identity and launch intent are immutable")
    if target.status is AttemptStatus.STARTED and target.started_at is None:
        raise ValidationError("STARTED attempts require started_at")
    if target.status in {AttemptStatus.SUCCEEDED, AttemptStatus.FAILED,
                         AttemptStatus.AMBIGUOUS, AttemptStatus.CANCELLED} and target.completed_at is None:
        raise ValidationError(f"{target.status.value} attempts require completed_at")
    if target.status is AttemptStatus.AMBIGUOUS and not target.detail.strip():
        raise ValidationError("AMBIGUOUS attempts require a recovery detail")
    if target.started_at and target.completed_at and target.completed_at < target.started_at:
        raise ValidationError("attempt completed_at cannot precede started_at")
    if previous.started_at is not None and previous.started_at != target.started_at:
        raise ValidationError("attempt launch timestamp is immutable")


def redact_text(value: str, maximum: int = 16_777_216, secrets: tuple[str, ...] = ()) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValidationError(f"evidence text must be text of at most {maximum} characters")
    if not value:
        return value
    result = value
    for secret in secrets:
        if len(secret) >= 8:
            result = result.replace(secret, "<REDACTED>")
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else ''}<REDACTED>", result)
    return result


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def environment_secrets(environment: dict[str, str] | None = None) -> tuple[str, ...]:
    values = dict(os.environ)
    values.update(environment or {})
    return tuple(value for key, value in values.items()
                 if re.search(r"(?:API_KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL)$", key, re.I) and len(value) >= 8)


def safe_excerpt(value: str, maximum: int = 32768, secrets: tuple[str, ...] = ()) -> str:
    return redact_text(value.replace("\x00", "\\0"), secrets=secrets)[:maximum]


@dataclass(frozen=True)
class ExecutionAttempt:
    attempt_id: str
    task_id: str
    task_revision: int
    role: Role
    provider: Provider
    executable: str
    workspace_root: str
    status: AttemptStatus
    created_at: str
    requested_model: str | None = None
    cli_version: str | None = None
    provider_session_id: str | None = None
    reported_model: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    returncode: int | None = None
    signal: int | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None
    result_status: str | None = None
    detail: str = ""
    revision: int = 1
    schema_version: int = 1

    def __post_init__(self) -> None:
        task_id(self.attempt_id)
        task_id(self.task_id)
        bounded_int(self.task_revision, "attempt task_revision", 1, 2**63 - 1)
        bounded_int(self.revision, "attempt revision", 1, 2**63 - 1)
        bounded_int(self.schema_version, "attempt schema_version", 1, 1)
        if not isinstance(self.role, Role) or not isinstance(self.provider, Provider):
            raise ValidationError("attempt role/provider must be enum values")
        text(self.executable, "attempt executable", 4096)
        text(self.workspace_root, "attempt workspace root", 4096)
        if not isinstance(self.status, AttemptStatus):
            raise ValidationError("attempt status must be an AttemptStatus value")
        utc_timestamp(self.created_at)
        for value, name in ((self.started_at, "attempt started_at"), (self.completed_at, "attempt completed_at")):
            if value is not None:
                utc_timestamp(value)
        for value, name, maximum in ((self.requested_model, "requested model", 256),
                                     (self.cli_version, "CLI version", 256),
                                     (self.provider_session_id, "provider session", 4096),
                                     (self.reported_model, "reported model", 256),
                                     (self.result_status, "result status", 128)):
            if value is not None:
                text(value, name, maximum)
        if self.returncode is not None and type(self.returncode) is not int:
            raise ValidationError("attempt returncode must be an integer or None")
        if self.signal is not None:
            bounded_int(self.signal, "attempt signal", 1, 255)
        bounded_int(self.stdout_bytes, "attempt stdout_bytes", 0, 16_777_216)
        bounded_int(self.stderr_bytes, "attempt stderr_bytes", 0, 16_777_216)
        for value, name in ((self.stdout_sha256, "stdout digest"), (self.stderr_sha256, "stderr digest")):
            if value is not None and _DIGEST.fullmatch(value) is None:
                raise ValidationError(f"{name} must be a lowercase SHA-256 digest")
        if not isinstance(self.detail, str) or len(self.detail) > 4096 or "\x00" in self.detail:
            raise ValidationError("attempt detail must be text of at most 4096 characters")

    def to_dict(self) -> dict[str, Any]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result.update(role=self.role.value, provider=self.provider.value, status=self.status.value)
        return result

    @classmethod
    def from_dict(cls, value: object) -> "ExecutionAttempt":
        data = dict(exact_keys(value, {field.name for field in fields(cls)}, "ExecutionAttempt"))
        try:
            data["role"] = Role(data["role"])
            data["provider"] = Provider(data["provider"])
            data["status"] = AttemptStatus(data["status"])
            return cls(**data)
        except (TypeError, ValueError) as exc:
            raise ValidationError("invalid ExecutionAttempt enum or field type") from exc


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    attempt_id: str
    kind: str
    created_at: str
    payload_json: str
    digest_sha256: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        task_id(self.evidence_id)
        task_id(self.attempt_id)
        text(self.kind, "evidence kind", 128)
        utc_timestamp(self.created_at)
        bounded_int(self.schema_version, "evidence schema_version", 1, 1)
        if _DIGEST.fullmatch(self.digest_sha256) is None:
            raise ValidationError("evidence digest must be a lowercase SHA-256 digest")
        payload = parse_canonical_json(self.payload_json)
        if not isinstance(payload, dict):
            raise ValidationError("evidence payload must be canonical JSON")
        if digest_text(self.payload_json) != self.digest_sha256:
            raise ValidationError("evidence digest does not match its payload")
        if redact_text(self.payload_json) != self.payload_json:
            raise ValidationError("evidence contains unredacted credential-like text")

    def to_dict(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, "attempt_id": self.attempt_id,
                "kind": self.kind, "created_at": self.created_at,
                "payload": parse_canonical_json(self.payload_json), "digest_sha256": self.digest_sha256,
                "schema_version": self.schema_version}


def output_evidence(attempt: ExecutionAttempt, invocation: Invocation,
                    execution: ExecutionResult, result: NormalizedResult,
                    created_at: str, artifact_refs: tuple[ArtifactRef, ...] = (),
                    secrets: tuple[str, ...] = (), artifact_error: str | None = None) -> EvidenceRecord:
    import uuid
    stdout = safe_excerpt(execution.stdout, secrets=secrets)
    stderr = safe_excerpt(execution.stderr, secrets=secrets)
    payload = {
        "schema_version": 1,
        "argv": [safe_excerpt(argument, 4096, secrets) for argument in invocation.argv],
        "cwd": attempt.workspace_root,
        "provider": invocation.provider.value,
        "requested_model": attempt.requested_model,
        "cli_version": attempt.cli_version,
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at,
        "output_mode": invocation.output_mode.value,
        "returncode": execution.returncode,
        "execution_status": execution.status.value,
        "result_status": result.status.value,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_bytes": execution.stdout_bytes,
        "stderr_bytes": execution.stderr_bytes,
        "stdout_sha256": execution.stdout_sha256 or digest_text(execution.stdout),
        "stderr_sha256": execution.stderr_sha256 or digest_text(execution.stderr),
        "stdout_saved_sha256": digest_text(stdout),
        "stderr_saved_sha256": digest_text(stderr),
        "content": safe_excerpt(result.content, secrets=secrets) if result.content is not None else None,
        "structured_output": safe_excerpt(result.structured_output, secrets=secrets) if result.structured_output is not None else None,
        "reported_model": safe_excerpt(result.reported_model, 256, secrets) if result.reported_model is not None else None,
        "session_id": safe_excerpt(result.session_id, 4096, secrets) if result.session_id is not None else None,
        "artifact_refs": [artifact.to_dict() for artifact in artifact_refs],
        "artifacts_verified": artifact_error is None,
        "artifact_error": safe_excerpt(artifact_error, 4096, secrets) if artifact_error else None,
        "stdout_truncated": len(execution.stdout) > 32768,
        "stderr_truncated": len(execution.stderr) > 32768,
        "capture_limited": execution.status.value == "output_limit",
        "cleanup_complete": execution.cleanup_complete,
        "redacted_fields": [],
        "detail": safe_excerpt(execution.detail, 4096, secrets),
    }
    for key, original in (("stdout", execution.stdout), ("stderr", execution.stderr),
                          ("content", result.content), ("structured_output", result.structured_output),
                          ("reported_model", result.reported_model), ("session_id", result.session_id),
                          ("detail", execution.detail)):
        if original is not None and safe_excerpt(original, 16_777_216, secrets) != original:
            payload["redacted_fields"].append(key)
    if payload["argv"] != list(invocation.argv):
        payload["redacted_fields"].append("argv")
    payload_json = canonical_json(payload)
    return EvidenceRecord(uuid.uuid4().hex, attempt.attempt_id, "provider_result",
                          created_at, payload_json, digest_text(payload_json))
