import hashlib
import os
import tempfile
from pathlib import Path
from stat import S_ISREG

from .errors import ConflictError, ValidationError
from .evidence import digest_text
from .packets import ArtifactRef
from .validation import canonical_json, exact_keys, parse_canonical_json
from .workflow_contracts import WorkflowSpec


def workspace_manifest(root: Path) -> str:
    if root.resolve(strict=True) != root or not root.is_dir():
        raise ValidationError("workflow workspace must remain an existing canonical directory")
    entries = {}
    total = 0
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            raise ValidationError(f"workflow workspace contains a symlink: {relative}")
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


def apply_response(spec: WorkflowSpec, manifest_json: str, response: object) -> tuple[str, tuple[ArtifactRef, ...]]:
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
    try:
        for relative, content in replacements.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.parent.resolve(strict=True).is_relative_to(root):
                raise ValidationError("owned output parent escapes workspace")
            descriptor, temporary = tempfile.mkstemp(prefix=".diamond-stage-", dir=target.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            staged.append((Path(temporary), target))
        for temporary, target in staged:
            relative = str(target.relative_to(root))
            existing = manifest.get(relative)
            if target.is_symlink() or target.parent.resolve(strict=True) != target.parent:
                raise ConflictError("owned file path changed during staging")
            actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
            if actual != (existing["sha256"] if existing else None):
                raise ConflictError("owned file changed during staging; preserve the current edit")
            os.replace(temporary, target)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
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
