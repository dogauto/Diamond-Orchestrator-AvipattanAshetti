import hashlib
import os
import tempfile
from pathlib import Path
from stat import S_IMODE, S_ISREG

from .errors import ConflictError, StorageError, ValidationError

PROTECTED_PATHS = (
    Path("config"),
    Path("scripts"),
    Path("tests"),
    Path("src/diamond/cli.py"),
    Path("src/diamond/paths.py"),
    Path("src/diamond/store.py"),
    Path("src/diamond/schema.py"),
    Path("src/diamond/diagnostics.py"),
    Path("src/diamond/supervisor.py"),
    Path("src/diamond/self_improve.py"),
    Path("src/diamond/workspace.py"),
    Path("src/diamond/sandbox.py"),
    Path("src/diamond/packets.py"),
    Path("src/diamond/workflow.py"),
    Path("src/diamond/workflow_contracts.py"),
    Path("src/diamond/workflow_store.py"),
    Path("src/diamond/delivery.py"),
    Path("src/diamond/delivery_contracts.py"),
    Path("src/diamond/delivery_gates.py"),
    Path("src/diamond/delivery_store.py"),
    Path("src/diamond/controller.py"),
    Path("src/diamond/occupancy.py"),
    Path("src/diamond/evidence.py"),
    Path("src/diamond/skills.py"),
    Path("src/diamond/adapters.py"),
)


def validate_owned_paths(project: Path, owned_paths: tuple[str, ...]) -> None:
    if not owned_paths:
        raise ValidationError("self-improvement requires at least one owned source path")
    for value in owned_paths:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValidationError("self-improvement paths must be project-relative without traversal")
        if not path.is_relative_to("src") or str(path) != value:
            raise ValidationError("self-improvement paths must be canonical paths under src")
        if any(path == protected or path.is_relative_to(protected) for protected in PROTECTED_PATHS):
            raise ValidationError(f"self-improvement cannot modify protected path: {value}")
        target = (project / path).resolve()
        if not target.is_relative_to(project.resolve()):
            raise ValidationError(f"self-improvement path escapes the project: {value}")
        current = project
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                raise ValidationError(f"self-improvement cannot own symlink paths: {value}")
        if target.suffix != ".py" or not target.is_file():
            raise ValidationError(f"self-improvement paths must be existing Python source files: {value}")


def publish_verified_changes(project: Path, checkpoint: Path, owned_paths: tuple[str, ...]) -> None:
    """Publish a verified shadow only when every live file is still unchanged.

    Files are staged before replacement and an exception restores already
    replaced files. This is exception-safe; crash recovery remains governed by
    the persisted workflow quarantine and explicit reconciliation.
    """
    validate_owned_paths(project, owned_paths)
    entries: list[tuple[Path, Path, bytes, int, bytes, int]] = []
    for relative in owned_paths:
        source = checkpoint / "workspace" / relative
        original_source = checkpoint / relative
        target = project / relative
        if not source.is_file() or not original_source.is_file() or not target.is_file():
            raise ValidationError(f"self-improvement publication requires regular files: {relative}")
        source_info = source.stat()
        original_info = original_source.stat()
        target_info = target.stat()
        if (not S_ISREG(source_info.st_mode) or not S_ISREG(original_info.st_mode)
                or not S_ISREG(target_info.st_mode)):
            raise ValidationError(f"self-improvement publication requires regular files: {relative}")
        source_bytes = source.read_bytes()
        original_bytes = original_source.read_bytes()
        entries.append((source, target, source_bytes, S_IMODE(source_info.st_mode), original_bytes,
                        S_IMODE(original_info.st_mode)))
    for source, target, _, _, original, _ in entries:
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(original).digest():
            raise ConflictError(f"live source changed after verification: {target.relative_to(project)}")
    staged: list[tuple[Path, Path, bytes, int]] = []
    installed: list[tuple[Path, bytes, int]] = []
    try:
        for source, target, content, mode, _, _ in entries:
            descriptor, name = tempfile.mkstemp(prefix=".diamond-improve-", dir=target.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fchmod(handle.fileno(), mode)
                os.fsync(handle.fileno())
            staged.append((temporary, target, content, mode))
        for temporary, target, content, mode in staged:
            if target.read_bytes() != next(original for _, candidate, _, _, original, _ in entries if candidate == target):
                raise ConflictError(f"live source changed during publication: {target.relative_to(project)}")
            os.replace(temporary, target)
            installed.append((target, content, mode))
    except BaseException as exc:
        failures: list[str] = []
        for target, content, mode in reversed(installed):
            original = next(original for _, candidate, _, _, original, _ in entries if candidate == target)
            try:
                if target.read_bytes() == content:
                    descriptor, name = tempfile.mkstemp(prefix=".diamond-improve-rollback-", dir=target.parent)
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(original)
                        handle.flush()
                        os.fchmod(handle.fileno(), next(old_mode for _, candidate, _, _, _, old_mode in entries if candidate == target))
                        os.fsync(handle.fileno())
                    os.replace(name, target)
                else:
                    failures.append(str(target))
            except OSError as rollback_error:
                failures.append(f"{target}: {rollback_error}")
        for temporary, _, _, _ in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                failures.append(f"{temporary}: {cleanup_error}")
        if failures:
            raise StorageError("self-improvement publication rollback incomplete: " + "; ".join(failures)) from exc
        raise
    finally:
        for temporary, _, _, _ in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
