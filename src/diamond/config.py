import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .domain import Provider, Role
from .errors import ConfigError, ValidationError
from .validation import bounded_int, exact_keys, text

MAX_CONFIG_BYTES = 1_048_576


@dataclass(frozen=True)
class ControllerConfig:
    max_workers: int
    task_timeout_seconds: int
    correction_round_limit: int

    def __post_init__(self) -> None:
        bounded_int(self.max_workers, "controller.max_workers", 1, 64)
        bounded_int(self.task_timeout_seconds, "controller.task_timeout_seconds", 1, 86_400)
        bounded_int(self.correction_round_limit, "controller.correction_round_limit", 0, 100)


@dataclass(frozen=True)
class ProviderConfig:
    executable: str
    enabled: bool

    def __post_init__(self) -> None:
        text(self.executable, "provider executable", 4096)
        if type(self.enabled) is not bool:
            raise ValidationError("provider enabled must be boolean")
        if any(char in self.executable for char in "\n\r"):
            raise ValidationError("executable must be one command name or absolute path")
        if os.sep in self.executable and not Path(self.executable).is_absolute():
            raise ValidationError("executable paths must be absolute; command names resolve through PATH")


@dataclass(frozen=True)
class DiamondConfig:
    schema_version: int
    project_name: str
    controller: ControllerConfig
    providers: Mapping[Provider, ProviderConfig]
    roles: Mapping[Role, Provider]

    def __post_init__(self) -> None:
        bounded_int(self.schema_version, "config schema_version", 1, 1)
        text(self.project_name, "project_name", 256)
        if not isinstance(self.controller, ControllerConfig):
            raise ValidationError("controller must be a ControllerConfig")
        if not isinstance(self.providers, Mapping) or not isinstance(self.roles, Mapping):
            raise ValidationError("providers and roles must be mappings")
        providers, roles = dict(self.providers), dict(self.roles)
        if set(providers) != set(Provider) or not all(isinstance(key, Provider) for key in providers):
            raise ValidationError("configuration requires exactly six Provider enum keys")
        if not all(isinstance(value, ProviderConfig) for value in providers.values()):
            raise ValidationError("provider entries must be ProviderConfig values")
        if set(roles) != set(Role) or not all(isinstance(key, Role) for key in roles):
            raise ValidationError("configuration requires exactly nine Role enum keys")
        if not all(isinstance(value, Provider) and providers[value].enabled for value in roles.values()):
            raise ValidationError("every role must map to an enabled Provider enum value")
        object.__setattr__(self, "providers", MappingProxyType(providers))
        object.__setattr__(self, "roles", MappingProxyType(roles))

    def resolve_provider(self, role: Role, explicit: Provider | None = None) -> Provider:
        if not isinstance(role, Role) or explicit is not None and not isinstance(explicit, Provider):
            raise ValidationError("role/provider must be approved enum values")
        result = explicit if explicit is not None else self.roles[role]
        if not self.providers[result].enabled:
            raise ValidationError(f"provider {result.value} is disabled; select an enabled provider")
        return result


def parse_config(source: str) -> DiamondConfig:
    if not isinstance(source, str):
        raise ConfigError("config source must be UTF-8 text")
    try:
        if len(source) > MAX_CONFIG_BYTES or len(source.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ConfigError("config source must be at most 1 MiB of UTF-8 text")
        data = exact_keys(tomllib.loads(source), {"schema_version", "project_name", "controller", "providers", "roles"}, "config")
        controller = ControllerConfig(**exact_keys(data["controller"], {"max_workers", "task_timeout_seconds", "correction_round_limit"}, "controller"))
        provider_data = exact_keys(data["providers"], {provider.value for provider in Provider}, "providers")
        providers = {provider: ProviderConfig(**exact_keys(provider_data[provider.value], {"executable", "enabled"}, f"providers.{provider.value}")) for provider in Provider}
        role_data = exact_keys(data["roles"], {role.value for role in Role}, "roles")
        roles = {role: Provider(role_data[role.value]) for role in Role}
        return DiamondConfig(data["schema_version"], data["project_name"], controller, providers, roles)
    except RecursionError as exc:
        raise ConfigError("configuration nesting exceeds supported parser depth; simplify the TOML structure") from exc
    except (tomllib.TOMLDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid schema-1 configuration: {exc}") from exc


def load_config(path: Path) -> DiamondConfig:
    try:
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
            raise ConfigError(f"config must be a regular TOML file of at most 1 MiB: {path}")
        return parse_config(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
