from pathlib import Path

from .errors import ValidationError

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
        if any(path == protected or path.is_relative_to(protected) for protected in PROTECTED_PATHS):
            raise ValidationError(f"self-improvement cannot modify protected path: {value}")
        target = (project / path).resolve()
        if not target.is_relative_to(project.resolve()):
            raise ValidationError(f"self-improvement path escapes the project: {value}")
        if target.suffix != ".py" or not target.is_file():
            raise ValidationError(f"self-improvement paths must be existing Python source files: {value}")
