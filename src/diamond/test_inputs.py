import hashlib
import sys
import sysconfig
from pathlib import Path

from .errors import ConflictError, ValidationError
from .validation import canonical_json, parse_canonical_json
from .workflow_contracts import WorkflowSpec


def test_paths(spec: WorkflowSpec) -> tuple[set[Path], set[Path]]:
    root = Path(spec.workspace_root)
    paths = set()
    directories = set()
    for test in spec.tests:
        paths.add(Path(test.argv[0]))
        for index, argument in enumerate(test.argv[1:], 1):
            path = Path(argument)
            target = path if path.is_absolute() else root / path
            pattern = test.argv[index - 1] in {"-p", "--pattern"} or argument.startswith("--pattern=")
            if target.is_file() or target.suffix == ".py" and not pattern:
                paths.add(target)
            if target.is_dir() and target.resolve() != root:
                directories.add(target)
            if argument.replace(".", "").replace("_", "").isalnum() and test.argv[index - 1] == "-m":
                module = root.joinpath(*argument.split("."))
                first = root / argument.split(".")[0]
                if any((root / owned).is_relative_to(first) or root / owned == first.with_suffix(".py") for owned in spec.owned_paths):
                    raise ValidationError("owned source must not shadow its trusted module test runner")
                options = (module.with_suffix(".py"), module / "__main__.py", module / "__init__.py")
                if not any(path.is_file() for path in options):
                    if argument.split(".")[0] not in sys.stdlib_module_names or Path(test.argv[0]).resolve() != Path(sys.executable).resolve():
                        raise ValidationError("module test runner must be a target harness or the declared interpreter's stdlib module")
                    module = Path(sysconfig.get_path("stdlib")).joinpath(*argument.split("."))
                    options = (module.with_suffix(".py"), module / "__main__.py", module / "__init__.py")
                if module.is_dir():
                    directories.add(module)
                paths.update(path for path in options if path.is_file())
                paths.update(parent / "__init__.py" for parent in module.parents
                             if parent.is_relative_to(root) and (parent / "__init__.py").is_file())
    return paths, directories


def _entry(path: Path) -> dict:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size > 64 * 1024 * 1024:
        raise ValidationError("trusted test input must be a bounded regular file")
    contents = resolved.read_bytes()
    return {"resolved_path": str(resolved), "size_bytes": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}


def _directory_entry(path: Path) -> dict:
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValidationError("trusted test directory is missing")
    inventory = {}
    total = 0
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise ValidationError("trusted test directory must not contain symlinks")
        relative = str(item.relative_to(root))
        if item.is_dir():
            inventory[relative] = {"kind": "directory"}
        else:
            entry = _entry(item)
            total += entry["size_bytes"]
            inventory[relative] = {"kind": "file", "sha256": entry["sha256"], "size_bytes": entry["size_bytes"]}
        if len(inventory) > 2048 or total > 64 * 1024 * 1024:
            raise ValidationError("trusted test directory exceeds its bounded inventory budget")
    digest = hashlib.sha256(canonical_json(inventory).encode()).hexdigest()
    return {"resolved_path": str(root), "inventory_sha256": digest, "entries": len(inventory)}


def capture_test_inputs(spec: WorkflowSpec) -> str:
    paths, directories = test_paths(spec)
    if not 1 <= len(paths) + len(directories) <= 64:
        raise ValidationError("trusted test input set exceeds the bounded file budget")
    targets = {(Path(spec.workspace_root) / path).resolve() for path in spec.owned_paths}
    if any(path.resolve() in targets for path in paths) or any(target.is_relative_to(directory.resolve()) for target in targets for directory in directories):
        raise ValidationError("integrated source must not replace trusted verification inputs")
    result = canonical_json({"files": {str(path.absolute()): _entry(path) for path in sorted(paths)},
                             "directories": {str(path.absolute()): _directory_entry(path) for path in sorted(directories)}})
    if len(result) > 16384:
        raise ValidationError("trusted input manifest exceeds its context budget")
    return result


def verify_test_inputs(value: str) -> None:
    entries = parse_canonical_json(value)
    if type(entries) is not dict or set(entries) != {"files", "directories"}:
        raise ValidationError("trusted input manifest is invalid")
    for path, expected in entries["files"].items():
        if _entry(Path(path)) != expected:
            raise ConflictError("trusted verification input changed since acceptance")
    for path, expected in entries["directories"].items():
        if _directory_entry(Path(path)) != expected:
            raise ConflictError("trusted verification directory inventory changed since acceptance")
