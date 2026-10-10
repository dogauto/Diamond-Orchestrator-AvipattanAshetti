from dataclasses import dataclass
from os import stat_result
from pathlib import Path
from stat import S_ISDIR

from .errors import StorageError, ValidationError
from .validation import text


@dataclass(frozen=True)
class ProjectPaths:
    project: Path
    config: Path
    state: Path
    database: Path


def storage_stat(path: Path) -> stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StorageError(f"cannot inspect storage path {path}: {exc}; check directory access and permissions") from exc


def resolve_storage_path(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise StorageError(f"cannot resolve storage path {path}: {exc}; check directory access and symlinks") from exc


def resolve_paths(project: str, config: str | None = None, state_dir: str | None = None) -> ProjectPaths:
    try:
        root = Path(text(project, "project path", 4096)).expanduser().resolve(strict=True)
        if not S_ISDIR(root.stat().st_mode):
            raise ValidationError("project path must be an existing directory")

        def resolve(value: str, name: str, storage: bool = False) -> Path:
            candidate = Path(text(value, name, 4096)).expanduser()
            if ".." in candidate.parts:
                raise ValidationError(f"{name} must not contain traversal components")
            path = candidate if candidate.is_absolute() else root / candidate
            return resolve_storage_path(path) if storage else path.resolve()

        config_path = resolve(config if config is not None else "config/diamond.toml", "config path")
        state = resolve(state_dir if state_dir is not None else ".diamond", "state directory", storage=True)
        if state_dir is None and not state.is_relative_to(root):
            raise ValidationError("default .diamond resolves outside project; remove the escaping symlink")
        state_info = storage_stat(state)
        if state_info is not None and not S_ISDIR(state_info.st_mode):
            raise ValidationError("state directory must be a directory")
        database = resolve_storage_path(state / "diamond.sqlite3")
        if not database.is_relative_to(state):
            raise ValidationError("database symlink escapes the selected state directory")
        return ProjectPaths(root, config_path, state, database)
    except FileNotFoundError as exc:
        raise ValidationError(f"cannot resolve project/config/state paths: {exc}") from exc
    except (OSError, RuntimeError) as exc:
        raise StorageError(f"cannot access project/config/state paths: {exc}; check directory permissions") from exc
