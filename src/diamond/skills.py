import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from .domain import Role
from .errors import ValidationError
from .validation import text


_DIGEST = re.compile(r"[0-9a-f]{64}")


class SkillValidation(str, Enum):
    VERIFIED = "verified"
    MISSING = "missing"
    CHANGED = "changed"
    PREREQUISITE_UNAVAILABLE = "prerequisite_unavailable"


def _file_digest(path: Path) -> str:
    try:
        if not path.is_file():
            raise ValidationError(f"skill source is not a regular file: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(65_536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise ValidationError(f"skill source cannot be read: {path}: {exc}") from exc


@dataclass(frozen=True)
class SkillRecord:
    name: str
    source_path: str
    supplied_version: str
    digest_sha256: str
    scope: str
    applicable_roles: tuple[Role, ...]
    dependencies: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        text(self.name, "skill name", 256)
        text(self.source_path, "skill source path", 4096)
        text(self.supplied_version, "skill version", 256)
        text(self.scope, "skill scope", 4096)
        if not Path(self.source_path).is_absolute() or "\x00" in self.source_path:
            raise ValidationError("skill source path must be an absolute path without NUL")
        if _DIGEST.fullmatch(self.digest_sha256) is None:
            raise ValidationError("skill digest must be a lowercase SHA-256 hex string")
        if type(self.applicable_roles) is not tuple or not self.applicable_roles:
            raise ValidationError("skill must apply to at least one role")
        if any(not isinstance(role, Role) for role in self.applicable_roles):
            raise ValidationError("skill applicable roles must be Role values")
        for name, values in (("skill dependencies", self.dependencies),
                             ("skill capabilities", self.required_capabilities)):
            if type(values) is not tuple or len(values) > 64:
                raise ValidationError(f"{name} must be an immutable tuple of at most 64 values")
            for value in values:
                text(value, name, 256)

    @classmethod
    def from_file(cls, name: str, source_path: Path, supplied_version: str,
                  scope: str, applicable_roles: Iterable[Role],
                  dependencies: Iterable[str] = (),
                  required_capabilities: Iterable[str] = ()) -> "SkillRecord":
        if not isinstance(source_path, Path):
            raise ValidationError("skill source path must be a Path")
        try:
            path = source_path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValidationError(f"skill source path cannot be resolved: {source_path}") from exc
        return cls(name, str(path), supplied_version, _file_digest(path), scope,
                   tuple(applicable_roles), tuple(dependencies), tuple(required_capabilities))

    def verify(self, role: Role, available_capabilities: frozenset[str]) -> SkillValidation:
        if role not in self.applicable_roles:
            raise ValidationError(f"skill {self.name} is not applicable to role {role.value}")
        path = Path(self.source_path)
        if not path.is_file():
            raise ValidationError(f"skill {self.name} source is missing: {self.source_path}")
        if _file_digest(path) != self.digest_sha256:
            raise ValidationError(f"skill {self.name} source digest changed")
        missing = sorted(set(self.required_capabilities) - set(available_capabilities))
        if missing:
            raise ValidationError(f"skill {self.name} prerequisites unavailable: {', '.join(missing)}")
        return SkillValidation.VERIFIED

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source_path": self.source_path,
            "supplied_version": self.supplied_version,
            "digest_sha256": self.digest_sha256,
            "scope": self.scope,
            "applicable_roles": [role.value for role in self.applicable_roles],
            "dependencies": list(self.dependencies),
            "required_capabilities": list(self.required_capabilities),
        }


@dataclass(frozen=True)
class SkillRegistry:
    records: tuple[SkillRecord, ...]

    def __post_init__(self) -> None:
        if type(self.records) is not tuple:
            raise ValidationError("skill registry records must be an immutable tuple")
        names = [record.name for record in self.records]
        if any(not isinstance(record, SkillRecord) for record in self.records):
            raise ValidationError("skill registry records must be SkillRecord values")
        if len(set(names)) != len(names):
            raise ValidationError("skill registry contains duplicate names")
        object.__setattr__(self, "_records", MappingProxyType(dict(zip(names, self.records))))

    def get(self, name: str) -> SkillRecord:
        text(name, "skill name", 256)
        try:
            return self._records[name]  # type: ignore[attr-defined]
        except KeyError as exc:
            raise ValidationError(f"required skill is not registered: {name}") from exc

    def resolve(self, names: Iterable[str], role: Role,
                available_capabilities: Iterable[str] = ()) -> tuple[SkillRecord, ...]:
        if not isinstance(role, Role):
            raise ValidationError("skill resolution role must be a Role value")
        capabilities = frozenset(available_capabilities)
        if any(not isinstance(capability, str) for capability in capabilities):
            raise ValidationError("available skill capabilities must be text")
        ordered: list[SkillRecord] = []
        visited: set[str] = set()
        active: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in active:
                raise ValidationError(f"skill dependency cycle includes: {name}")
            active.add(name)
            record = self.get(name)
            for dependency in record.dependencies:
                visit(dependency)
            record.verify(role, capabilities)
            active.remove(name)
            visited.add(name)
            ordered.append(record)

        for name in names:
            visit(name)
        return tuple(ordered)
