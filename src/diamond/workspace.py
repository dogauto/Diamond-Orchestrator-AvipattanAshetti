import fnmatch
import hashlib
import os
import re
import tempfile
from pathlib import Path
from stat import S_IMODE, S_ISREG
from types import TracebackType

from .errors import ConflictError, StorageError, ValidationError
from .evidence import digest_text
from .packets import ArtifactRef
from .validation import canonical_json, exact_keys, parse_canonical_json
from .workflow_contracts import WorkflowSpec


def _should_skip_path(path: Path, root: Path) -> bool:
    """Check if a path should be excluded from the workspace manifest.

    Skips:
    - Files matching .gitignore patterns
    - Common runtime state directories (.diamond, .swarm, .claude-flow, .bash_history, etc.)
    - Hidden files/dirs that are not Python source/tests
    """
    # First, check .gitignore in the root directory
    gitignore_path = root / ".gitignore"
    if gitignore_path.is_file():
        try:
            gitignore_rules = gitignore_path.read_text().splitlines()
            # Compute path relative to root
            rel = str(path.relative_to(root))
            for rule in gitignore_rules:
                rule = rule.strip()
                if not rule or rule.startswith("#"):
                    continue
                # Support simple patterns: exact match, *, **, /
                if rule == rel:
                    return True
                # Handle trailing slash (directory)
                if rule.endswith("/") and rel.startswith(rule.rstrip("/")):
                    return True
                # Handle * and ** wildcards with simple matching
                pattern = fnmatch(rel, rule) if '*' in rule or '?' in rule else False
                if pattern:
                    return True
        except Exception:
            pass  # If .gitignore can't be read, continue without it

    # Always skip common runtime state directories
    runtime_names = {".diamond", ".swarm", ".claude-flow", ".diamond_history",
                     ".bash_history", ".git", "node_modules", "__pycache__"}
    if path.name in runtime_names:
        return True

    # Skip .pyc files
    if path.suffix == ".pyc":
        return True

    return False


def workspace_manifest(root: Path) -> str:
    # Resolve to absolute, then normalize symlinks (e.g. /var → /private/var on macOS)
    resolved = root.resolve()
    if root != resolved:
        raise ValidationError("workflow workspace must be a canonical directory (no symlinks)")
    if not resolved.is_dir():
        raise ValidationError("workflow workspace must remain an existing canonical directory")
    entries = {}
    total = 0
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            raise ValidationError(f"workflow workspace contains a symlink: {relative}")
        if _should_skip_path(path, root):
            continue
        if path.is_dir():
            entries[relative] = {"kind": "directory"}
        elif S_ISREG(path.stat().st_mode):
            size = path.stat().st_size
            total += size
            if total > 32 * 1024 * 1024:
                raise ValidationError("bounded workflow workspace exceeds 32 MiB")
            entries[relative] = {"kind": "file", "size_bytes": size,
                                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            raise ValidationError("bounded workflow requires only regular files and directories")
        if len(entries) > 2048:
            raise ValidationError("bounded workflow workspace exceeds 2048 entries")
    return canonical_json(entries)


def verify_workspace(spec: WorkflowSpec, expected_manifest: str) -> Path:
    root = Path(spec.workspace_root)
    if workspace_manifest(root) != expected_manifest:
        raise ConflictError("workflow workspace changed since acceptance; preserve edits and re-accept")
    return root


class PublicationRollbackError(StorageError):
    pass


class Publication:
    """Undo caught publication failures, including a surrounding ledger commit failure.

    This in-memory undo log cannot make filesystem changes crash-atomic with SQLite.
    The caller's active workflow/delivery reservation must remain held until completion.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.originals: dict[Path, tuple[bytes | None, int | None]] = {}
        self.installed: list[tuple[Path, bytes, int]] = []
        self.temporaries: set[Path] = set()
        self.directories: list[Path] = []

    def __enter__(self) -> "Publication":
        return self

    def _state(self, target: Path) -> tuple[bytes | None, int | None]:
        if target.is_symlink() or target.parent.resolve(strict=True) != target.parent:
            raise ConflictError("owned file path changed during publication")
        if not target.exists():
            return None, None
        info = target.stat()
        if not S_ISREG(info.st_mode):
            raise ConflictError("owned output is no longer a regular file")
        return target.read_bytes(), S_IMODE(info.st_mode)

    def _write(self, target: Path, content: bytes, mode: int, prefix: str) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=prefix, dir=target.parent)
        temporary = Path(name)
        self.temporaries.add(temporary)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        return temporary

    def stage(self, target: Path, content: bytes, expected_digest: str | None) -> tuple[Path, Path, bytes, int]:
        missing = []
        parent = target.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        if parent.resolve(strict=True) != parent or not parent.is_relative_to(self.root):
            raise ConflictError("owned output parent changed or escapes workspace")
        for directory in reversed(missing):
            directory.mkdir()
            self.directories.append(directory)
        original, mode = self._state(target)
        if (hashlib.sha256(original).hexdigest() if original is not None else None) != expected_digest:
            raise ConflictError("owned file changed during staging; preserve the current edit")
        self.originals[target] = original, mode
        output_mode = mode if mode is not None else 0o600
        return self._write(target, content, output_mode, ".diamond-stage-"), target, content, output_mode

    def install(self, temporary: Path, target: Path, content: bytes, mode: int) -> None:
        if self._state(target) != self.originals[target]:
            raise ConflictError("owned file changed during staging; preserve the current edit")
        os.replace(temporary, target)
        self.temporaries.remove(temporary)
        self.installed.append((target, content, mode))

    def apply(self, spec: WorkflowSpec, manifest_json: str, response: object) -> tuple[str, tuple[ArtifactRef, ...]]:
        return _apply_response(spec, manifest_json, response, self)

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 traceback: TracebackType | None) -> None:
        if kind is None:
            return
        failures = []
        for target, content, mode in reversed(self.installed):
            recovery = None
            try:
                current = self._state(target)
                original, original_mode = self.originals[target]
                if original is not None:
                    recovery = self._write(target, original, original_mode, ".diamond-rollback-")
                if current != (content, mode):
                    raise ConflictError("published file changed; preserving intervening edit")
                if recovery is None:
                    target.unlink()
                else:
                    os.replace(recovery, target)
                    self.temporaries.remove(recovery)
            except (OSError, ConflictError) as exc:
                if recovery is not None:
                    self.temporaries.discard(recovery)
                failures.append(f"{target}: {exc}" + (f"; original retained at {recovery}" if recovery is not None else ""))
        for temporary in sorted(self.temporaries):
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                failures.append(f"{temporary}: {exc}")
        for directory in reversed(self.directories):
            try:
                directory.rmdir()
            except OSError as exc:
                failures.append(f"{directory}: {exc}")
        if failures:
            raise PublicationRollbackError("publication rollback incomplete; retain quarantine and reconcile: " + "; ".join(failures)) from error


def apply_response(spec: WorkflowSpec, manifest_json: str, response: object) -> tuple[str, tuple[ArtifactRef, ...]]:
    with Publication(Path(spec.workspace_root)) as publication:
        return publication.apply(spec, manifest_json, response)


def _apply_response(spec: WorkflowSpec, manifest_json: str, response: object,
                    publication: Publication) -> tuple[str, tuple[ArtifactRef, ...]]:
    root = verify_workspace(spec, manifest_json)
    data = exact_keys(response, {"files", "summary"}, "Builder file response")
    if type(data["summary"]) is not str or not data["summary"].strip() or len(data["summary"]) > 4096:
        raise ValidationError("file response requires a bounded summary")
    if type(data["files"]) is not list or not 1 <= len(data["files"]) <= len(spec.owned_paths):
        raise ValidationError("file response must contain bounded owned-file replacements")
    manifest = parse_canonical_json(manifest_json)
    replacements = {}
    for item in data["files"]:
        entry = exact_keys(item, {"relative_path", "base_sha256", "content"}, "file replacement")
        relative = entry["relative_path"]
        if not isinstance(relative, str) or relative not in spec.owned_paths or relative in replacements:
            raise ValidationError("file response contains a duplicate or unowned path")
        existing = manifest.get(relative)
        if existing is not None and existing["kind"] != "file":
            raise ValidationError("owned output replaces a directory")
        expected = existing["sha256"] if existing else None
        if entry["base_sha256"] != expected:
            raise ConflictError("file replacement base digest is stale")
        content = entry["content"]
        if type(content) is not str or "\x00" in content or len(content.encode()) > 131072:
            raise ValidationError("file response content must be bounded UTF-8 source without NUL")
        replacements[relative] = content
    source_bytes = 0
    for relative in spec.owned_paths:
        if relative in replacements:
            source_bytes += len(replacements[relative].encode("utf-8"))
        elif relative in manifest:
            source_bytes += manifest[relative]["size_bytes"]
    if source_bytes > 32768:
        raise ValidationError("proposed owned source exceeds the 32 KiB correction-context budget")
    future_files = [{"relative_path": relative, "base_sha256": "0" * 64,
        "content": replacements.get(relative, (root / relative).read_text(encoding="utf-8") if relative in manifest else None)}
        for relative in spec.owned_paths]
    if len(canonical_json(future_files)) > 32768:
        raise ValidationError("serialized owned source exceeds the correction-context budget")
    staged = []
    for relative, content in replacements.items():
        existing = manifest.get(relative)
        staged.append(publication.stage(root / relative, content.encode("utf-8"), existing["sha256"] if existing else None))
    for replacement in staged:
        publication.install(*replacement)
    after = workspace_manifest(root)
    before_entries = manifest
    after_entries = parse_canonical_json(after)
    allowed_parents = {str(parent) for path in spec.owned_paths for parent in Path(path).parents if str(parent) != "."}
    for relative in set(before_entries) | set(after_entries):
        if before_entries.get(relative) != after_entries.get(relative) and relative not in replacements:
            if relative not in allowed_parents or after_entries.get(relative) != {"kind": "directory"}:
                raise ConflictError("workspace changed outside owned file replacements")
    artifacts = tuple(ArtifactRef(relative, relative, after_entries[relative]["sha256"],
                                  after_entries[relative]["size_bytes"], "source") for relative in replacements)
    return after, artifacts
