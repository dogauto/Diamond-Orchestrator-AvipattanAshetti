from pathlib import Path

from .errors import ExecutionGuardError, ValidationError

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
    Path("src/diamond/dispatch.py"),
    Path("src/diamond/config.py"),
    Path("src/diamond/delivery_cli.py"),
    Path("src/diamond/integration.py"),
    Path("src/diamond/test_inputs.py"),
    Path("src/diamond/routing.py"),
    Path("src/diamond/provider_health.py"),
    Path("src/diamond/daily_improve.py"),
    Path("src/diamond/improvement_schema.py"),
    Path("src/diamond/improvement_store.py"),
    Path("src/diamond/improvement_evidence.py"),
    Path("src/diamond/improvement_publication.py"),
    Path("src/diamond/self_improve_cli.py"),
    Path("src/diamond/agent_profiles.py"),
    Path("src/diamond/agent_compiler.py"),
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
    raise ExecutionGuardError("verification alone cannot publish source; use self-improve review and publish")
