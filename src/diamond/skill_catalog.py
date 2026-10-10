import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .domain import Role
from .errors import ValidationError
from .skills import SkillRecord, SkillRegistry
from .validation import bounded_int, exact_keys, text

CATALOG_RELATIVE_PATH = Path("config") / "skills" / "catalog.toml"
MAX_CATALOG_BYTES = 1_048_576
MAX_SKILL_BYTES = 16_384
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class SkillProvenance:
    name: str
    source_url: str
    source_commit: str
    git_blob_sha1: str
    source_sha256: str
    license: str

    def __post_init__(self) -> None:
        text(self.name, "skill provenance name", 256)
        text(self.source_url, "skill source URL", 4096)
        if not self.source_url.startswith("https://"):
            raise ValidationError("skill source URL must use HTTPS")
        if _HEX40.fullmatch(self.source_commit) is None:
            raise ValidationError("skill source commit must be a 40-character lowercase SHA-1")
        if _HEX40.fullmatch(self.git_blob_sha1) is None:
            raise ValidationError("skill Git blob must be a 40-character lowercase SHA-1")
        if _HEX64.fullmatch(self.source_sha256) is None:
            raise ValidationError("skill source SHA-256 must be a lowercase SHA-256 hex string")
        text(self.license, "skill license", 128)


@dataclass(frozen=True)
class SkillCatalog:
    registry: SkillRegistry
    provenance: tuple[SkillProvenance, ...]
    role_names: Mapping[Role, tuple[str, ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.registry, SkillRegistry) or type(self.provenance) is not tuple:
            raise ValidationError("skill catalog requires a registry and immutable provenance")
        if len(self.provenance) != len(self.registry.records):
            raise ValidationError("skill catalog provenance must match registry records")
        if any(not isinstance(item, SkillProvenance) for item in self.provenance):
            raise ValidationError("skill catalog provenance must contain SkillProvenance values")
        names = tuple(record.name for record in self.registry.records)
        if tuple(item.name for item in self.provenance) != names:
            raise ValidationError("skill catalog provenance order must match registry records")
        mapping = dict(self.role_names)
        if set(mapping) != set(Role):
            raise ValidationError("skill catalog requires one role entry for every Role")
        for role, values in mapping.items():
            if not isinstance(role, Role) or type(values) is not tuple:
                raise ValidationError("skill catalog role entries must be immutable tuples")
            if len(set(values)) != len(values) or any(value not in names for value in values):
                raise ValidationError("skill catalog role entries must name registered skills once")
        object.__setattr__(self, "role_names", MappingProxyType(mapping))
        object.__setattr__(self, "_by_name", MappingProxyType(dict(zip(names, self.provenance))))

    def for_role(self, role: Role, available_capabilities: tuple[str, ...] = ()) -> tuple[SkillRecord, ...]:
        if not isinstance(role, Role) or type(available_capabilities) is not tuple:
            raise ValidationError("skill resolution requires a Role and immutable capabilities")
        return self.registry.resolve(self.role_names[role], role, available_capabilities)

    def provenance_for(self, name: str) -> SkillProvenance:
        text(name, "skill name", 256)
        try:
            return self._by_name[name]  # type: ignore[attr-defined]
        except KeyError as exc:
            raise ValidationError(f"skill provenance is not registered: {name}") from exc

    def guidance_for(self, role: Role) -> tuple[dict[str, str], ...]:
        records = self.for_role(role)
        result: list[dict[str, str]] = []
        for record in records:
            source = Path(record.source_path)
            try:
                content_bytes = source.read_bytes()
                if len(content_bytes) > MAX_SKILL_BYTES:
                    raise ValidationError(f"skill {record.name} exceeds the bounded guidance size")
                content = content_bytes.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValidationError(f"skill {record.name} guidance cannot be read") from exc
            if "\x00" in content:
                raise ValidationError(f"skill {record.name} guidance contains NUL")
            provenance = self.provenance_for(record.name)
            result.append({
                "name": record.name,
                "version": record.supplied_version,
                "digest_sha256": record.digest_sha256,
                "source_url": provenance.source_url,
                "license": provenance.license,
                "content": content,
            })
        return tuple(result)


def _relative_source(root: Path, value: object) -> Path:
    text(value, "skill source path", 4096)
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValidationError("skill source path must be relative and cannot traverse")
    try:
        resolved = (root / relative).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValidationError(f"skill source path cannot be resolved: {value}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValidationError(f"skill source path escapes or is not a file: {value}")
    return resolved


def _list_of_text(value: object, name: str, maximum: int = 64) -> tuple[str, ...]:
    if type(value) is not list or len(value) > maximum:
        raise ValidationError(f"{name} must be a bounded array of text")
    result = tuple(text(item, name, 256) for item in value)
    if len(set(result)) != len(result):
        raise ValidationError(f"{name} must not contain duplicates")
    return result


def load_skill_catalog(project_root: Path) -> SkillCatalog:
    if not isinstance(project_root, Path):
        raise ValidationError("skill catalog project root must be a Path")
    try:
        root = project_root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValidationError("skill catalog project root cannot be resolved") from exc
    path = root / CATALOG_RELATIVE_PATH
    if not path.exists():
        empty = {role: () for role in Role}
        return SkillCatalog(SkillRegistry(()), (), empty)
    try:
        catalog_path = path.resolve(strict=True)
        if not catalog_path.is_relative_to(root):
            raise ValidationError("skill catalog escapes the project root")
        if not catalog_path.is_file() or catalog_path.stat().st_size > MAX_CATALOG_BYTES:
            raise ValidationError("skill catalog must be a regular file of at most 1 MiB")
        data = tomllib.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValidationError(f"skill catalog cannot be read: {path}") from exc
    catalog = exact_keys(data, {"schema_version", "skills"}, "skill catalog")
    bounded_int(catalog["schema_version"], "skill catalog schema_version", 1, 1)
    entries = catalog["skills"]
    if type(entries) is not list or len(entries) > 64:
        raise ValidationError("skill catalog requires at most 64 skills")
    records: list[SkillRecord] = []
    provenance: list[SkillProvenance] = []
    role_names = {role: [] for role in Role}
    for entry in entries:
        fields = exact_keys(entry, {"name", "source_path", "source_url", "source_commit", "git_blob_sha1",
                                    "source_sha256", "license", "supplied_version", "scope", "applicable_roles",
                                    "dependencies", "required_capabilities"}, "skill catalog entry")
        name = text(fields["name"], "skill name", 256)
        roles_text = _list_of_text(fields["applicable_roles"], "applicable roles")
        try:
            roles = tuple(Role(value) for value in roles_text)
        except ValueError as exc:
            raise ValidationError(f"skill {name} contains an unknown role") from exc
        dependencies = _list_of_text(fields["dependencies"], "skill dependencies")
        capabilities = _list_of_text(fields["required_capabilities"], "skill capabilities")
        source = _relative_source(root, fields["source_path"])
        record = SkillRecord.from_file(name, source, text(fields["supplied_version"], "skill version", 256),
                                       text(fields["scope"], "skill scope", 4096), roles, dependencies, capabilities)
        declared_sha256 = text(fields["source_sha256"], "skill source SHA-256", 128)
        if record.digest_sha256 != declared_sha256:
            raise ValidationError(f"skill {name} source digest does not match the catalog")
        item = SkillProvenance(name, text(fields["source_url"], "skill source URL", 4096),
                               text(fields["source_commit"], "skill source commit", 64),
                               text(fields["git_blob_sha1"], "skill Git blob", 64), declared_sha256,
                               text(fields["license"], "skill license", 128))
        records.append(record)
        provenance.append(item)
        for role in roles:
            role_names[role].append(name)
    return SkillCatalog(SkillRegistry(tuple(records)), tuple(provenance),
                        {role: tuple(names) for role, names in role_names.items()})
