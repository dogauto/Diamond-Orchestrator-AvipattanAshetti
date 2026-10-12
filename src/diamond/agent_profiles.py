"""Digest-pinned advisory profiles for the fixed Claude Builder pilot."""

import hashlib
import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from stat import S_ISREG

from .domain import Provider, Role
from .errors import ConflictError, ValidationError
from .validation import canonical_json, exact_keys, text


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def checked_digest(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValidationError("profile digest must be a lowercase SHA-256 digest")
    return value


def normalize_tags(tags) -> tuple[str, ...]:
    if type(tags) not in (tuple, list) or len(tags) > 64:
        raise ValidationError("task tags must be a tuple or array of at most 64 names")
    if any(type(tag) is not str or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", tag) is None for tag in tags):
        raise ValidationError("task tags must be canonical lowercase names")
    if len(set(tags)) != len(tags):
        raise ValidationError("task tags must be distinct")
    return tuple(sorted(tags))


def read_source(path: Path, root: Path, limit: int) -> str:
    """Read bounded UTF-8 bytes, rejecting symlinks in every path component."""
    try:
        if not path.is_absolute() or not path.is_relative_to(root) or ".." in path.parts:
            raise ValidationError("profile input must stay inside its declared root")
        if path.resolve(strict=True) != path or not S_ISREG(path.stat().st_mode):
            raise ValidationError("profile input must be a canonical regular file without symlinks")
        with path.open("rb") as source:
            content = source.read(limit + 1)
        if len(content) > limit:
            raise ValidationError("profile input exceeds its byte budget")
        result = content.decode("utf-8")
        text(result, "profile input", limit)
        return result
    except (OSError, UnicodeError, RuntimeError) as exc:
        raise ValidationError(f"cannot read profile input: {path}") from exc


@dataclass(frozen=True)
class ProfileRecord:
    profile_id: str
    version: str
    role: Role
    provider: Provider
    required_tags: tuple[str, ...]
    conflicts_with: tuple[str, ...]
    source_path: str
    instruction: str
    digest_sha256: str
    review_status: str
    reviewed_by: str
    provenance: str
    license: str
    evaluation_status: str

    def __post_init__(self):
        if type(self.profile_id) is not str or re.fullmatch(r"[a-z][a-z0-9-]{2,127}", self.profile_id) is None:
            raise ValidationError("profile ID is invalid")
        text(self.version, "profile version", 64)
        if self.role is not Role.BUILDER or self.provider is not Provider.CLAUDE:
            raise ValidationError("M2 profiles are limited to the fixed Claude Builder route")
        for tags in (self.required_tags, self.conflicts_with):
            if type(tags) is not tuple or tags != normalize_tags(tags):
                raise ValidationError("profile tags must be canonical tuples")
        if not self.required_tags or set(self.required_tags) & set(self.conflicts_with):
            raise ValidationError("profile needs nonconflicting required tags")
        if not Path(self.source_path).is_absolute():
            raise ValidationError("profile source path must be absolute")
        text(self.instruction, "profile instruction", 8192)
        if digest(self.instruction) != checked_digest(self.digest_sha256):
            raise ValidationError("profile content differs from its pinned digest")
        if self.review_status != "contract-reviewed" or self.evaluation_status != "unevaluated":
            raise ValidationError("M2 profiles require a contract review and explicit unevaluated status")
        for name in ("reviewed_by", "provenance", "license"):
            text(getattr(self, name), name, 512)

    def to_dict(self):
        data = {field.name: getattr(self, field.name) for field in fields(self)}
        data.update(role=self.role.value, provider=self.provider.value,
                    required_tags=list(self.required_tags), conflicts_with=list(self.conflicts_with))
        return data


@dataclass(frozen=True)
class ProfileSelection:
    role: Role
    provider: Provider
    task_tags: tuple[str, ...]
    catalog_digest: str
    profile_id: str | None
    profile_version: str | None
    profile_digest: str | None
    reasons: tuple[str, ...]
    alternatives: tuple[tuple[str, str], ...]

    @property
    def status(self):
        return "selected" if self.profile_id else "legacy"

    def to_dict(self):
        return {"role": self.role.value, "provider": self.provider.value, "task_tags": list(self.task_tags),
                "catalog_digest": self.catalog_digest, "profile_id": self.profile_id, "profile_version": self.profile_version,
                "profile_digest": self.profile_digest, "status": self.status, "reasons": list(self.reasons),
                "alternatives": [{"profile_id": name, "reason": reason} for name, reason in self.alternatives]}

    @property
    def decision_digest(self):
        return digest(canonical_json(self.to_dict()))


@dataclass(frozen=True)
class ProfileCatalog:
    records: tuple[ProfileRecord, ...]
    catalog_path: Path
    catalog_digest: str

    @classmethod
    def load(cls, project: Path):
        try:
            root = project.expanduser().resolve(strict=True)
            path = root / "config/agents/catalog.toml"
            source = read_source(path, root, 65536)
            data = exact_keys(tomllib.loads(source), {"schema_version", "profiles"}, "profile catalog")
            if type(data["schema_version"]) is not int or data["schema_version"] != 1:
                raise ValidationError("unsupported profile catalog version")
            if type(data["profiles"]) is not list or not 1 <= len(data["profiles"]) <= 64:
                raise ValidationError("profile catalog requires 1..64 entries")
            records = []
            for raw in data["profiles"]:
                entry = dict(exact_keys(raw, {"id", "version", "role", "provider", "required_tags", "conflicts_with",
                    "source", "sha256", "review_status", "reviewed_by", "provenance", "license", "evaluation_status"}, "profile entry"))
                relative = Path(text(entry.pop("source"), "profile source", 256))
                if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".md":
                    raise ValidationError("profile source must be relative Markdown without traversal")
                profile_path = path.parent / relative
                instruction = read_source(profile_path, path.parent, 8192)
                records.append(ProfileRecord(entry.pop("id"), entry.pop("version"), Role(entry.pop("role")),
                    Provider(entry.pop("provider")), normalize_tags(entry.pop("required_tags")),
                    normalize_tags(entry.pop("conflicts_with")), str(profile_path), instruction, entry.pop("sha256"), **entry))
            if len({item.profile_id for item in records}) != len(records):
                raise ValidationError("profile catalog contains duplicate IDs")
            return cls(tuple(sorted(records, key=lambda item: item.profile_id)), path, digest(source))
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise ValidationError("invalid profile catalog or source") from exc

    def verify(self):
        current = self.load(self.catalog_path.parents[2])
        if current != self:
            raise ValidationError("profile catalog or source changed after selection")

    def select(self, role: Role, provider: Provider, tags) -> ProfileSelection:
        if not isinstance(role, Role) or not isinstance(provider, Provider):
            raise ValidationError("profile selection requires validated role/provider enums")
        self.verify()
        tags = normalize_tags(tags)
        matches, alternatives = [], []
        for item in self.records:
            if item.role is not role or item.provider is not provider:
                reason = "different fixed role/provider"
            elif not set(item.required_tags) <= set(tags):
                reason = "missing required tags: " + ", ".join(sorted(set(item.required_tags) - set(tags)))
            elif set(item.conflicts_with) & set(tags):
                raise ConflictError(f"task tags contradict profile {item.profile_id}")
            else:
                reason = "required tags matched"
                matches.append(item)
            alternatives.append((item.profile_id, reason))
        if len(matches) > 1:
            raise ConflictError("task tags select multiple reviewed profiles")
        selected = matches[0] if matches else None
        return ProfileSelection(role, provider, tags, self.catalog_digest,
            selected.profile_id if selected else None, selected.version if selected else None,
            selected.digest_sha256 if selected else None,
            ("required tags matched on fixed route" if selected else "no match; preserve legacy packet and prompt",),
            tuple(alternatives))

    def get(self, profile_id: str) -> ProfileRecord:
        for item in self.records:
            if item.profile_id == profile_id:
                return item
        raise ValidationError(f"profile not registered: {profile_id}")
