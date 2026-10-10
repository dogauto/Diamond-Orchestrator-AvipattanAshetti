import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .adapters import Invocation
from .domain import Provider, Role, TaskSpec
from .errors import ValidationError
from .skills import SkillRecord, SkillRegistry
from .validation import bounded_int, canonical_json, task_id, text


_DIGEST = re.compile(r"[0-9a-f]{64}")


def _digest(value: str, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValidationError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _relative_path(value: str, name: str) -> str:
    text(value, name, 4096)
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValidationError(f"{name} must be a relative path without traversal")
    return value


@dataclass(frozen=True)
class ArtifactRef:
    name: str
    relative_path: str
    digest_sha256: str
    size_bytes: int
    kind: str

    def __post_init__(self) -> None:
        text(self.name, "artifact name", 256)
        _relative_path(self.relative_path, "artifact path")
        _digest(self.digest_sha256, "artifact digest")
        bounded_int(self.size_bytes, "artifact size", 0, 1_073_741_824)
        text(self.kind, "artifact kind", 128)

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "relative_path": self.relative_path,
                "digest_sha256": self.digest_sha256, "size_bytes": self.size_bytes,
                "kind": self.kind}


@dataclass(frozen=True)
class OutputContract:
    format: str
    required_fields: tuple[str, ...]
    max_bytes: int

    def __post_init__(self) -> None:
        if self.format not in {"json", "text"}:
            raise ValidationError("output contract format must be json or text")
        if type(self.required_fields) is not tuple or len(self.required_fields) > 64:
            raise ValidationError("output contract fields must be an immutable tuple of at most 64 values")
        for field in self.required_fields:
            text(field, "output contract field", 256)
        bounded_int(self.max_bytes, "output contract max_bytes", 1, 16_777_216)

    def to_dict(self) -> dict[str, object]:
        return {"format": self.format, "required_fields": list(self.required_fields),
                "max_bytes": self.max_bytes}

    def verify(self, content: str | None) -> None:
        if content is None or not content.strip() or len(content.encode("utf-8")) > self.max_bytes:
            raise ValidationError("assistant output is empty or exceeds its packet contract")
        if self.format == "json":
            try:
                value = json.loads(content)
            except (ValueError, RecursionError) as exc:
                raise ValidationError("assistant output does not match the JSON contract") from exc
            if not isinstance(value, dict) or not set(self.required_fields) <= set(value):
                raise ValidationError("assistant output is missing required JSON fields")


@dataclass(frozen=True)
class PacketContext:
    plan_revision: int
    plan_digest: str
    base_digest: str
    config_digest: str

    def __post_init__(self) -> None:
        bounded_int(self.plan_revision, "current plan revision", 1, 2**63 - 1)
        for value in (self.plan_digest, self.base_digest, self.config_digest):
            _digest(value, "current context digest")


@dataclass(frozen=True)
class TaskPacket:
    task_id: str
    attempt_id: str
    objective: str
    role: Role
    provider: Provider
    requested_model: str | None
    plan_revision: int
    plan_digest: str
    base_digest: str
    config_digest: str
    workspace_root: str
    owned_paths: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    skills: tuple[SkillRecord, ...]
    required_tests: tuple[str, ...]
    acceptance_conditions: tuple[str, ...]
    context_artifacts: tuple[ArtifactRef, ...]
    timeout_seconds: int
    correction_round_limit: int
    output_contract: OutputContract
    schema_version: int = 1
    task_revision: int = 1
    provider_tools: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        task_id(self.task_id)
        task_id(self.attempt_id)
        text(self.objective, "packet objective", 1_000_000)
        if not isinstance(self.role, Role) or not isinstance(self.provider, Provider):
            raise ValidationError("packet role/provider must be enum values")
        if self.requested_model is not None:
            text(self.requested_model, "requested model", 256)
        bounded_int(self.schema_version, "packet schema_version", 1, 1)
        bounded_int(self.task_revision, "task revision", 1, 2**63 - 1)
        bounded_int(self.plan_revision, "plan revision", 1, 2**63 - 1)
        for value, name in ((self.plan_digest, "plan digest"), (self.base_digest, "base digest"),
                            (self.config_digest, "config digest")):
            _digest(value, name)
        root = Path(self.workspace_root)
        if not root.is_absolute() or "\x00" in self.workspace_root:
            raise ValidationError("packet workspace root must be an absolute path without NUL")
        for name, values in (("owned paths", self.owned_paths), ("allowed tools", self.allowed_tools),
                             ("required tests", self.required_tests), ("acceptance conditions", self.acceptance_conditions)):
            if type(values) is not tuple or len(values) > 256:
                raise ValidationError(f"{name} must be an immutable tuple of at most 256 values")
            for value in values:
                _relative_path(value, name) if name == "owned paths" else text(value, name, 4096)
        if type(self.skills) is not tuple or any(not isinstance(skill, SkillRecord) for skill in self.skills):
            raise ValidationError("packet skills must be an immutable tuple of SkillRecord values")
        if len({skill.name for skill in self.skills}) != len(self.skills):
            raise ValidationError("packet skills must not contain duplicates")
        if type(self.context_artifacts) is not tuple or any(not isinstance(artifact, ArtifactRef) for artifact in self.context_artifacts):
            raise ValidationError("packet context artifacts must be an immutable tuple of ArtifactRef values")
        bounded_int(self.timeout_seconds, "packet timeout_seconds", 1, 86_400)
        bounded_int(self.correction_round_limit, "packet correction_round_limit", 0, 100)
        if not isinstance(self.output_contract, OutputContract):
            raise ValidationError("packet output_contract must be an OutputContract")
        if self.provider_tools is not None:
            if type(self.provider_tools) is not tuple or len(self.provider_tools) > 64:
                raise ValidationError("provider_tools must be an immutable tuple or None")
            for tool in self.provider_tools:
                text(tool, "provider tool", 128)

    @classmethod
    def from_task_spec(cls, spec: TaskSpec, attempt_id: str, workspace_root: Path,
                       plan_revision: int, plan_digest: str, base_digest: str,
                       config_digest: str, registry: SkillRegistry,
                       available_capabilities: Iterable[str],
                       output_contract: OutputContract,
                       requested_model: str | None = None,
                       task_revision: int = 1) -> "TaskPacket":
        if not isinstance(spec, TaskSpec):
            raise ValidationError("packet source must be a TaskSpec")
        skills = registry.resolve(spec.allowed_skills or (), spec.role, available_capabilities)
        return cls(
            task_id=spec.task_id,
            attempt_id=attempt_id,
            objective=spec.objective,
            role=spec.role,
            provider=spec.provider,
            requested_model=requested_model,
            plan_revision=plan_revision,
            plan_digest=plan_digest,
            base_digest=base_digest,
            config_digest=config_digest,
            workspace_root=str(workspace_root.expanduser().resolve(strict=True)),
            owned_paths=spec.owned_paths or (),
            allowed_tools=spec.allowed_tools or (),
            skills=skills,
            required_tests=spec.required_tests or (),
            acceptance_conditions=spec.acceptance_conditions or (),
            context_artifacts=(),
            timeout_seconds=spec.timeout_seconds or 900,
            correction_round_limit=spec.correction_round_limit or 0,
            output_contract=output_contract,
            task_revision=task_revision,
        )

    def verify_workspace(self) -> None:
        root = Path(self.workspace_root).resolve(strict=True)
        if not root.is_dir():
            raise ValidationError("packet workspace root must be an existing directory")
        for relative in self.owned_paths:
            target = (root / relative).resolve()
            if not target.is_relative_to(root):
                raise ValidationError(f"owned path escapes workspace: {relative}")
        for artifact in self.context_artifacts:
            target = (root / artifact.relative_path).resolve()
            if not target.is_relative_to(root):
                raise ValidationError(f"context artifact escapes workspace: {artifact.relative_path}")

    def verify_current(self, plan_revision: int, plan_digest: str, base_digest: str,
                       config_digest: str, task_revision: int | None = None) -> None:
        if task_revision is not None and self.task_revision != task_revision:
            raise ValidationError("task packet is stale; refresh and re-accept the packet")
        if (self.plan_revision, self.plan_digest, self.base_digest, self.config_digest) != \
                (plan_revision, plan_digest, base_digest, config_digest):
            raise ValidationError("task packet is stale; refresh and re-accept the packet")

    def verify(self, registry: SkillRegistry, available_capabilities: Iterable[str]) -> None:
        self.verify_workspace()
        current = registry.resolve((skill.name for skill in self.skills), self.role,
                                   available_capabilities)
        if current != self.skills:
            raise ValidationError("task packet skill set is stale or dependency order changed")
        for artifact in self.context_artifacts:
            path = Path(self.workspace_root) / artifact.relative_path
            if not path.is_file():
                raise ValidationError(f"context artifact is missing: {artifact.relative_path}")
            if path.stat().st_size != artifact.size_bytes:
                raise ValidationError(f"context artifact size changed: {artifact.relative_path}")
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(65_536), b""):
                    digest.update(chunk)
            if digest.hexdigest() != artifact.digest_sha256:
                raise ValidationError(f"context artifact digest changed: {artifact.relative_path}")

    def verify_output_artifacts(self, artifacts: Iterable[ArtifactRef]) -> tuple[ArtifactRef, ...]:
        """Validate produced artifacts against the packet workspace and ownership."""
        result = tuple(artifacts)
        root = Path(self.workspace_root).resolve(strict=True)
        owned = tuple(Path(path) for path in self.owned_paths)
        for artifact in result:
            if not isinstance(artifact, ArtifactRef):
                raise ValidationError("output artifacts must be ArtifactRef values")
            relative = Path(artifact.relative_path)
            if not any(relative == path or path in relative.parents for path in owned):
                raise ValidationError(f"output artifact is outside owned paths: {artifact.relative_path}")
            try:
                target = (root / relative).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValidationError(f"output artifact is missing or invalid: {artifact.relative_path}") from exc
            if not target.is_file() or not target.is_relative_to(root):
                raise ValidationError(f"output artifact is not a contained file: {artifact.relative_path}")
            actual_size = target.stat().st_size
            if actual_size != artifact.size_bytes:
                raise ValidationError(f"output artifact size changed: {artifact.relative_path}")
            digest = hashlib.sha256()
            with target.open("rb") as source:
                for chunk in iter(lambda: source.read(65_536), b""):
                    digest.update(chunk)
            if digest.hexdigest() != artifact.digest_sha256:
                raise ValidationError(f"output artifact digest changed: {artifact.relative_path}")
        return result

    def authorize_invocation(self, invocation: Invocation, cwd: Path) -> None:
        """Enforce the packet's provider, workspace, and executable allow-list."""
        if not isinstance(invocation, Invocation):
            raise ValidationError("invocation must be a validated Invocation")
        root = Path(self.workspace_root).resolve(strict=True)
        actual_cwd = cwd.resolve(strict=True)
        if actual_cwd != root:
            raise ValidationError("invocation cwd must equal the packet workspace root")
        if invocation.provider is not self.provider:
            raise ValidationError("invocation provider does not match the task packet")
        def resolve_tool(tool: str) -> Path | None:
            candidate = Path(tool)
            if candidate.is_absolute():
                return candidate.resolve(strict=True) if candidate.is_file() else None
            if len(candidate.parts) != 1:
                raise ValidationError("allowed executable must be an absolute path or PATH command name")
            result = shutil.which(tool)
            return Path(result).resolve(strict=True) if result else None
        executable = resolve_tool(invocation.argv[0])
        allowed = {resolve_tool(tool) for tool in self.allowed_tools}
        if executable is None or executable not in allowed:
            raise ValidationError("invocation executable is not allowed by the task packet")
        if invocation.requested_model != self.requested_model:
            raise ValidationError("invocation requested model does not match the task packet")
        if self.provider_tools is not None:
            if self.provider is not Provider.CLAUDE:
                raise ValidationError("exact provider-tool selection is unsupported for this provider")
            expected = " ".join(self.provider_tools)
            if ("--tools", expected) not in tuple(zip(invocation.argv, invocation.argv[1:])):
                raise ValidationError("invocation does not enforce the packet provider-tool list")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "objective": self.objective,
            "role": self.role.value,
            "provider": self.provider.value,
            "requested_model": self.requested_model,
            "plan_revision": self.plan_revision,
            "plan_digest": self.plan_digest,
            "base_digest": self.base_digest,
            "config_digest": self.config_digest,
            "workspace_root": self.workspace_root,
            "owned_paths": list(self.owned_paths),
            "allowed_tools": list(self.allowed_tools),
            "skills": [skill.to_dict() for skill in self.skills],
            "required_tests": list(self.required_tests),
            "acceptance_conditions": list(self.acceptance_conditions),
            "context_artifacts": [artifact.to_dict() for artifact in self.context_artifacts],
            "timeout_seconds": self.timeout_seconds,
            "correction_round_limit": self.correction_round_limit,
            "output_contract": self.output_contract.to_dict(),
            "task_revision": self.task_revision,
            "provider_tools": list(self.provider_tools) if self.provider_tools is not None else None,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    def packet_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
