import argparse
import hashlib
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from .delivery_cli import delivery_command
from .delivery_store import DeliveryStore
from .errors import ConflictError, ExecutionGuardError, NotFoundError
from .improvement_publication import project_lock, publish, rollback
from .improvement_store import ImprovementStore, SOURCE_ROOTS, source_manifest
from .occupancy import protect_workspace_writers
from .self_improve import validate_owned_paths
from .validation import canonical_json, command_id, task_id
from .workflow_cli import workflow_command
from .workflow_contracts import WorkflowStatus
from .workflow_store import WorkflowStore
from .workspace import workspace_manifest


def _create(args, paths, config, ledger):
    command_id(args.command_id)
    identifier = args.workflow_id or uuid5(NAMESPACE_URL, f"diamond:workflow-create:{args.command_id}").hex
    task_id(identifier)
    checkpoint = paths.state / "self-improve-checkpoints" / identifier
    shadow = checkpoint / "workspace"
    improvements = ImprovementStore(ledger)
    with project_lock(paths.project):
        validate_owned_paths(paths.project, tuple(args.owned_path))
        with ledger._connection(write=True) as connection:
            protect_workspace_writers(connection, paths.project)
        existing = checkpoint.exists()
        if existing:
            improvements.checkpoint(identifier, paths.project)
            workflow = WorkflowStore(ledger).get(identifier)
            if Path(workflow.spec.workspace_root) != shadow:
                raise ConflictError("checkpoint does not match its workflow")
        else:
            before = source_manifest(paths.project)
            checkpoint.mkdir(parents=True)
            for relative in args.owned_path:
                target = checkpoint / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(paths.project / relative, target)
            shadow.mkdir()
            for name in SOURCE_ROOTS:
                source = paths.project / name
                if source.is_dir():
                    shutil.copytree(source, shadow / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                elif source.is_file():
                    shutil.copy2(source, shadow / name)
            if source_manifest(paths.project) != before or source_manifest(shadow) != before:
                raise ConflictError("inputs changed during checkpoint capture; use a new command ID")
        delegated = SimpleNamespace(workflow_command="create", objective=args.objective, workspace=str(shadow),
            owned_path=args.owned_path, test=args.test, correction_limit=None, workflow_id=args.workflow_id,
            task_tag=[], trial_opt_in=None, command_id=args.command_id)
        result, code = workflow_command(delegated, paths.config, config, ledger)
        if not existing:
            improvements.register(identifier, paths.project, before, result["workflow"]["spec"]["config_digest"])
        result["checkpoint"] = str(checkpoint)
        return result, code


def _review(args, paths, config, ledger, checkpoint, workflow):
    if workflow.status is not WorkflowStatus.VERIFIED:
        raise ExecutionGuardError("independent review requires a verified workflow")
    command_id(args.command_id)
    identifier = uuid5(NAMESPACE_URL, f"diamond:delivery-create:{args.command_id}").hex
    target = checkpoint / "review"
    with project_lock(paths.project):
        ImprovementStore(ledger).require_active(args.workflow_id)
        try:
            previous = DeliveryStore(ledger).get(identifier)
        except NotFoundError:
            previous = None
        if previous is not None:
            if previous.spec.workspace_root != str(target):
                raise ConflictError("review command belongs to another improvement")
            return {"delivery": previous.to_dict(), "checkpoint": str(checkpoint)}, 0
        if target.exists():
            raise ConflictError("review target already exists; inspect the retained checkpoint")
        if workspace_manifest(checkpoint / "workspace") != workflow.manifest_json:
            raise ConflictError("verified candidate changed before review")
        shutil.copytree(checkpoint / "workspace", target)
        for relative in workflow.spec.owned_paths:
            source = checkpoint / relative
            if source.is_symlink() or source.resolve() != source:
                raise ConflictError("checkpoint original changed through a symlink")
            shutil.copy2(source, target / relative)
        if workspace_manifest(target) != workflow.spec.initial_manifest_json:
            raise ConflictError("review base differs from the accepted checkpoint")
        requirements = {"self-improvement": {"description": workflow.spec.objective,
                                            "tests": [test.name for test in workflow.spec.tests]}}
        delegated = SimpleNamespace(delivery_command="create", delivery_id=identifier, objective=workflow.spec.objective,
            workspace=str(target), component=[f"{args.workflow_id}:{workflow.revision}"],
            test=[canonical_json(test.to_dict()) for test in workflow.spec.tests],
            requirements=canonical_json(requirements), supersedes=None, command_id=args.command_id)
        return delivery_command(delegated, paths.config, config, ledger)


def self_improve_command(args: argparse.Namespace, paths, config, ledger):
    if args.improvement_command == "create":
        return _create(args, paths, config, ledger)
    task_id(args.workflow_id)
    checkpoint = paths.state / "self-improve-checkpoints" / args.workflow_id
    improvements = ImprovementStore(ledger)
    improvements.checkpoint(args.workflow_id, paths.project)
    workflow = WorkflowStore(ledger).get(args.workflow_id)
    if Path(workflow.spec.workspace_root) != checkpoint / "workspace":
        raise ConflictError("checkpoint does not match its workflow")
    action = args.improvement_command
    if action == "cancel":
        with project_lock(paths.project):
            improvements.cancel(args.workflow_id, args.reason)
        return {"workflow_id": args.workflow_id, "cancelled": True,
                "detail": "Publication revoked; reconcile any interrupted publication separately"}, 0
    if action in {"rollback", "reconcile"}:
        return rollback(paths.project, checkpoint, ledger, args.workflow_id), 0
    if action in {"run", "review", "publish"}:
        improvements.require_active(args.workflow_id)
    if action == "publish":
        digest = hashlib.sha256(paths.config.read_bytes()).hexdigest()
        record = publish(paths.project, checkpoint, ledger, args.workflow_id, args.delivery_id, digest)
        return {"publication": {key: value for key, value in record.items() if key != "intent"},
                "applied_to_project": list(workflow.spec.owned_paths)}, 0
    if action == "review":
        return _review(args, paths, config, ledger, checkpoint, workflow)
    delegated = SimpleNamespace(workflow_command={"status": "show", "events": "events", "run": "step"}[action],
                                workflow_id=args.workflow_id, expected_revision=getattr(args, "expected_revision", None))
    result, code = workflow_command(delegated, paths.config, config, ledger)
    result.update(checkpoint=str(checkpoint), workspace=workflow.spec.workspace_root)
    record = improvements.publication(args.workflow_id)
    result["publication"] = None if record is None else {key: value for key, value in record.items() if key != "intent"}
    if action == "run" and result["workflow"]["status"] == "VERIFIED":
        result["next_step"] = "self-improve review; independent delivery acceptance is required before publish"
    return result, code
