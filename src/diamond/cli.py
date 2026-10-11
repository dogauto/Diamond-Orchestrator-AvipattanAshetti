import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence
from uuid import NAMESPACE_URL, uuid4, uuid5

from . import __version__
from .config import load_config
from .diagnostics import doctor
from .domain import Provider, Role, TaskSpec
from .errors import DiamondError, ValidationError
from .paths import resolve_paths
from .store import EventStore
from .validation import command_id as validate_command_id
from .validation import task_id as validate_task_id


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValidationError(f"{message}; use --help for command syntax")


def parser() -> argparse.ArgumentParser:
    root = ArgumentParser(description="Flawless Diamond Piece: local ledger and bounded execution contracts")
    root.add_argument("--project", default=os.environ.get("DIAMOND_PROJECT_ROOT", str(Path(__file__).resolve().parents[2])),
                      help="existing project root; relative config/state paths resolve here")
    root.add_argument("--config", help="TOML path (default: selected project's config/diamond.toml)")
    root.add_argument("--state-dir", help="explicit state directory; external directories allowed for isolated tests; no '..' traversal")
    root.add_argument("--json", action="store_true", help="emit one schema-versioned JSON envelope")
    root.add_argument("--version", action="version", version=f"diamond {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-config", help="validate schema-1 TOML without launching providers")
    diagnostic = commands.add_parser("doctor", help="bounded --version probes; authentication NOT_CHECKED")
    from .provider_cli import register as register_providers
    register_providers(commands)
    project = commands.add_parser("project", help="automation-safe project inspection")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_commands.add_parser("status", help="return provider, workflow, and delivery status")
    smoke = commands.add_parser("smoke", help="explicit real provider tests in isolated temporary workspaces")
    smoke.add_argument("--provider", action="append", required=True, choices=[provider.value for provider in Provider])
    smoke.add_argument("--code-test", action="store_true", help="also test a Claude-generated Python utility with executable assertions")
    commands.add_parser("migrate", help="explicitly migrate an existing ledger to the current schema")
    backup = commands.add_parser("backup", help="create a new validated SQLite backup without overwriting files")
    backup.add_argument("--destination", required=True)
    tasks = commands.add_parser("task", help="create and inspect task records; no dispatch")
    task_commands = tasks.add_subparsers(dest="task_command", required=True)
    create = task_commands.add_parser("create")
    create.add_argument("--objective", required=True)
    create.add_argument("--role", choices=[role.value for role in Role], default=Role.BUILDER.value)
    create.add_argument("--provider", choices=[provider.value for provider in Provider])
    create.add_argument("--task-id", help="32 lowercase hex UUID characters")
    create.add_argument("--command-id", help="idempotency key: 1..128 letters/digits/. _ : -")
    listing = task_commands.add_parser("list")
    show = task_commands.add_parser("show")
    show.add_argument("task_id")
    events = task_commands.add_parser("events")
    events.add_argument("task_id")
    attempts = task_commands.add_parser("attempts")
    attempts.add_argument("task_id")
    evidence = task_commands.add_parser("evidence")
    evidence.add_argument("task_id")
    claims = task_commands.add_parser("claims")
    claims.add_argument("task_id")
    reconcile = task_commands.add_parser("reconcile")
    reconcile.add_argument("attempt_id")
    reconcile.add_argument("--command-id", required=True)
    reconcile.add_argument("--reason", required=True)
    workflows = commands.add_parser("workflow", help="bounded response-based Builder/Debugger workflows")
    workflow_commands = workflows.add_subparsers(dest="workflow_command", required=True)
    workflow_create = workflow_commands.add_parser("create")
    workflow_create.add_argument("--objective", required=True)
    workflow_create.add_argument("--workspace", required=True)
    workflow_create.add_argument("--owned-path", action="append", required=True)
    workflow_create.add_argument("--test", action="append", required=True, help="trusted TestCommand JSON: name, argv, timeout_seconds, output_limit")
    workflow_create.add_argument("--correction-limit", type=int)
    workflow_create.add_argument("--workflow-id")
    workflow_create.add_argument("--command-id", required=True)
    workflow_list = workflow_commands.add_parser("list")
    workflow_show = workflow_commands.add_parser("show")
    workflow_show.add_argument("workflow_id")
    workflow_status = workflow_commands.add_parser("status", help="machine-readable alias for show")
    workflow_status.add_argument("workflow_id")
    workflow_events = workflow_commands.add_parser("events", help="show immutable workflow event history")
    workflow_events.add_argument("workflow_id")
    workflow_step = workflow_commands.add_parser("step", help="explicitly invoke the routed Builder or Debugger for one step")
    workflow_step.add_argument("workflow_id")
    workflow_step.add_argument("--expected-revision", type=int, required=True)
    for name in ("run", "resume"):
        command = workflow_commands.add_parser(name, help="invoke one bounded workflow step")
        command.add_argument("workflow_id")
        command.add_argument("--expected-revision", type=int, required=True)
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    workflow_reconcile = workflow_commands.add_parser("reconcile", help="quarantine an interrupted step and retain ownership")
    workflow_reconcile.add_argument("workflow_id")
    workflow_reconcile.add_argument("--expected-revision", type=int, required=True)
    workflow_reconcile.add_argument("--reason", required=True)
    deliveries = commands.add_parser("delivery", help="candidate-bound integration, independent QA/review and scoped release audit")
    delivery_commands = deliveries.add_subparsers(dest="delivery_command", required=True)
    delivery_create = delivery_commands.add_parser("create")
    delivery_create.add_argument("--objective", required=True)
    delivery_create.add_argument("--workspace", required=True)
    delivery_create.add_argument("--component", action="append", required=True, help="verified workflow_id:revision")
    delivery_create.add_argument("--test", action="append", required=True, help="trusted TestCommand JSON")
    delivery_create.add_argument("--requirements", required=True, help="JSON requirement ID to description/tests mapping")
    delivery_create.add_argument("--supersedes", help="inherit findings from the latest rejected/stale delivery")
    delivery_create.add_argument("--delivery-id")
    delivery_create.add_argument("--command-id", required=True)
    delivery_list = delivery_commands.add_parser("list")
    delivery_reads = []
    for name in ("show", "validate", "certificate"):
        reading = delivery_commands.add_parser(name)
        reading.add_argument("delivery_id")
        delivery_reads.append(reading)
    delivery_status = delivery_commands.add_parser("status", help="machine-readable alias for show")
    delivery_status.add_argument("delivery_id")
    delivery_step = delivery_commands.add_parser("step", help="invoke exactly one explicitly routed ready stage")
    delivery_step.add_argument("delivery_id")
    delivery_step.add_argument("--expected-revision", type=int, required=True)
    for name in ("run", "resume"):
        command = delivery_commands.add_parser(name, help="invoke one bounded delivery stage")
        command.add_argument("delivery_id")
        command.add_argument("--expected-revision", type=int, required=True)
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    delivery_reconcile = delivery_commands.add_parser("reconcile", help="quarantine an interrupted delivery; retain ownership")
    delivery_reconcile.add_argument("delivery_id")
    delivery_reconcile.add_argument("--expected-revision", type=int, required=True)
    delivery_reconcile.add_argument("--reason", required=True)
    mcp = commands.add_parser("mcp", help="serve the controller through the MCP stdio transport")
    mcp.add_argument("--stdio", action="store_true", help="serve newline-delimited JSON-RPC over stdin/stdout")
    improvements = commands.add_parser("self-improve", help="gated, evidence-backed orchestrator improvement")
    improvement_commands = improvements.add_subparsers(dest="improvement_command", required=True)
    improvement_create = improvement_commands.add_parser("create")
    improvement_create.add_argument("--objective", required=True)
    improvement_create.add_argument("--owned-path", action="append", required=True)
    improvement_create.add_argument("--test", action="append", required=True)
    improvement_create.add_argument("--workflow-id")
    improvement_create.add_argument("--command-id", required=True)
    improvement_run = improvement_commands.add_parser("run")
    improvement_run.add_argument("workflow_id")
    improvement_run.add_argument("--expected-revision", type=int, required=True)
    improvement_status = improvement_commands.add_parser("status")
    improvement_status.add_argument("workflow_id")
    improvement_events = improvement_commands.add_parser("events")
    improvement_events.add_argument("workflow_id")
    improvement_rollback = improvement_commands.add_parser("rollback")
    improvement_rollback.add_argument("workflow_id")
    interactive = commands.add_parser("shell", help="start an interactive command shell")
    dashboard = commands.add_parser("dashboard", help="start a local web dashboard (provider/workflow/task status)")
    dashboard.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    autonomy = commands.add_parser("autonomy", help="bounded goal planning and verified queue execution")
    autonomy_commands = autonomy.add_subparsers(dest="autonomy_command", required=True)
    autonomy_plan = autonomy_commands.add_parser("plan", help="plan a goal and enqueue validated workflows")
    autonomy_plan.add_argument("--goal", required=True)
    autonomy_plan.add_argument("--workspace", required=True)
    autonomy_plan.add_argument("--owned-path", action="append", required=True)
    autonomy_plan.add_argument("--test", action="append", required=True)
    autonomy_plan.add_argument("--command-id", required=True)
    autonomy_commands.add_parser("run", help="run queued workflows with finite safety budgets").add_argument("--max-steps", type=int, default=8)
    autonomy_run = autonomy_commands.choices["run"]
    autonomy_run.add_argument("--max-failures", type=int, default=1)
    autonomy_commands.add_parser("stop", help="request a running autonomy loop to stop")
    autonomy_commands.add_parser("clear-stop", help="clear the durable autonomy stop request")
    for command in (validate, diagnostic, smoke, backup, create, listing, show, events, attempts, evidence, claims, reconcile,
                     workflow_create, workflow_list, workflow_show, workflow_status, workflow_events, workflow_step, workflow_reconcile,
                     delivery_create, delivery_list, delivery_step, delivery_reconcile, delivery_status, *delivery_reads,
                     project_commands.choices["status"], improvement_create, improvement_run, improvement_status, improvement_events,
                     improvement_rollback, autonomy_plan, autonomy_run, autonomy_commands.choices["stop"],
                     autonomy_commands.choices["clear-stop"]):
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return root


def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.command == "self-improve" and args.state_dir is None:
        args.state_dir = str(Path(tempfile.gettempdir()) / "flawless-diamond-self-improve-state")
    paths = resolve_paths(args.project, args.config, args.state_dir)
    needs_config = args.command not in {"migrate", "backup"}
    config = load_config(paths.config) if needs_config else None
    if args.command == "validate-config":
        assert config is not None
        return {"config": str(paths.config), "valid": True, "project_name": config.project_name}, 0
    if args.command == "doctor":
        assert config is not None
        probes = doctor(config)
        ok = all(result.status in {"available", "disabled"} for result in probes)
        return {"providers": [result.to_dict() for result in probes], "auth_status": "NOT_CHECKED",
                "workflow_execution": "UNAVAILABLE"}, 0 if ok else 1
    if args.command == "project":
        assert config is not None
        from .delivery_store import DeliveryStore
        from .skill_catalog import load_skill_catalog
        from .workflow_store import WorkflowStore
        store = EventStore(paths.database)
        catalog = load_skill_catalog(paths.project)
        skills = []
        for record in catalog.registry.records:
            provenance = catalog.provenance_for(record.name)
            skills.append({"name": record.name, "version": record.supplied_version,
                           "digest_sha256": record.digest_sha256, "license": provenance.license,
                           "source_url": provenance.source_url,
                           "applicable_roles": [role.value for role in record.applicable_roles]})
        return {"project": str(paths.project), "config": str(paths.config),
                "providers": [result.to_dict() for result in doctor(config)],
                "skills": skills,
                "workflows": [item.to_dict() for item in WorkflowStore(store).list()],
                "deliveries": [item.to_dict() for item in DeliveryStore(store).list()]}, 0
    if args.command == "provider":
        from .provider_cli import provider_command
        assert config is not None
        return provider_command(args, paths, config)
    store = EventStore(paths.database)
    if args.command == "autonomy":
        from .autonomy import AutonomyLimits, clear_stop, plan_and_enqueue, request_stop, run_queue
        assert config is not None
        if args.autonomy_command == "stop":
            request_stop(paths.state)
            return {"stop_requested": True, "path": str(paths.state / "autonomy.stop")}, 0
        if args.autonomy_command == "clear-stop":
            clear_stop(paths.state)
            return {"stop_requested": False}, 0
        if args.autonomy_command == "run":
            report = run_queue(config, paths.config, store, paths.state,
                               AutonomyLimits(args.max_steps, args.max_failures))
            return report, 0 if report["failures"] == 0 else 1
        workspace = Path(args.workspace).expanduser().resolve(strict=True)
        return plan_and_enqueue(args.goal, config, paths.config, paths.project, store, workspace,
                                tuple(args.owned_path), tuple(args.test), args.command_id), 0
    if args.command == "smoke":
        from .live import smoke
        assert config is not None
        result = asyncio.run(smoke(paths.project, config, paths.config, store,
                                   tuple(Provider(provider) for provider in args.provider), args.code_test))
        return result, 0 if result["ok"] else 1
    if args.command == "migrate":
        return {"schema_version": store.migrate()}, 0
    if args.command == "backup":
        return {"backup": str(store.backup(Path(args.destination)))}, 0
    if args.command == "workflow":
        if args.workflow_command == "status":
            args.workflow_command = "show"
        elif args.workflow_command in {"run", "resume"}:
            args.workflow_command = "step"
        from .workflow_cli import workflow_command
        assert config is not None
        return workflow_command(args, paths.config, config, store)
    if args.command == "delivery":
        if args.delivery_command == "status":
            args.delivery_command = "show"
        elif args.delivery_command in {"run", "resume"}:
            args.delivery_command = "step"
        from .delivery_cli import delivery_command
        assert config is not None
        return delivery_command(args, paths.config, config, store)
    if args.command == "self-improve":
        from types import SimpleNamespace
        from .self_improve import publish_verified_changes, validate_owned_paths
        from .workflow_cli import workflow_command
        from .workflow_store import WorkflowStore
        assert config is not None
        if args.improvement_command == "create":
            validate_command_id(args.command_id)
            validate_owned_paths(paths.project, tuple(args.owned_path))
            checkpoint_workflow_id = args.workflow_id or uuid5(NAMESPACE_URL, f"diamond:workflow-create:{args.command_id}").hex
            validate_task_id(checkpoint_workflow_id)
            checkpoint = paths.state / "self-improve-checkpoints" / checkpoint_workflow_id
            shadow = checkpoint / "workspace"
            if checkpoint.exists():
                existing = WorkflowStore(store).get(checkpoint_workflow_id)
                if Path(existing.spec.workspace_root) != shadow.resolve():
                    raise ValidationError("self-improvement checkpoint does not match its workflow")
            else:
                checkpoint.mkdir(parents=True)
                for relative in args.owned_path:
                    source = paths.project / relative
                    target = checkpoint / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                shutil.copytree(paths.project / "src", shadow / "src")
                if (paths.project / "tests").is_dir():
                    shutil.copytree(paths.project / "tests", shadow / "tests")
                for directory in ("config", "scripts"):
                    source_directory = paths.project / directory
                    if source_directory.is_dir():
                        shutil.copytree(source_directory, shadow / directory)
                (shadow / "tests").mkdir(exist_ok=True)
                (shadow / "tests" / "__init__.py").write_text(
                    "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parents[1] / 'src'))\n",
                    encoding="utf-8")
            delegated = SimpleNamespace(
                workflow_command="create", objective=args.objective, workspace=str(shadow),
                owned_path=args.owned_path, test=args.test, correction_limit=None,
                workflow_id=args.workflow_id, command_id=args.command_id)
        elif args.improvement_command == "run":
            delegated = SimpleNamespace(workflow_command="step", workflow_id=args.workflow_id,
                                        expected_revision=args.expected_revision)
        elif args.improvement_command in {"status", "events"}:
            delegated = SimpleNamespace(workflow_command="show", workflow_id=args.workflow_id)
            if args.improvement_command == "events":
                delegated.workflow_command = "events"
        else:
            workflow = WorkflowStore(store).get(args.workflow_id)
            validate_owned_paths(paths.project, workflow.spec.owned_paths)
            checkpoint = paths.state / "self-improve-checkpoints" / args.workflow_id
            if not checkpoint.is_dir():
                raise ValidationError("self-improvement checkpoint is missing; refusing rollback")
            for relative in workflow.spec.owned_paths:
                source = checkpoint / relative
                target = paths.project / relative
                if not source.is_file():
                    raise ValidationError(f"self-improvement checkpoint is incomplete: {relative}")
            for relative in workflow.spec.owned_paths:
                source = checkpoint / relative
                target = paths.project / relative
                shutil.copy2(source, target)
            return {"workflow": workflow.to_dict(), "rolled_back": list(workflow.spec.owned_paths)}, 0
        result = workflow_command(delegated, paths.config, config, store)
        if args.improvement_command == "create":
            workflow_id = result[0]["workflow"]["spec"]["workflow_id"]
            result[0]["checkpoint"] = str(paths.state / "self-improve-checkpoints" / workflow_id)
        elif args.improvement_command == "run":
            workflow = WorkflowStore(store).get(args.workflow_id)
            if workflow.status.value == "VERIFIED":
                checkpoint = paths.state / "self-improve-checkpoints" / args.workflow_id
                publish_verified_changes(paths.project, checkpoint, workflow.spec.owned_paths)
                result[0]["applied_to_project"] = list(workflow.spec.owned_paths)
        elif args.improvement_command in {"status", "events"}:
            workflow = WorkflowStore(store).get(args.workflow_id)
            checkpoint = paths.state / "self-improve-checkpoints" / args.workflow_id
            result[0]["checkpoint"] = str(checkpoint)
            result[0]["workspace"] = workflow.spec.workspace_root
        return result
    if args.task_command == "create":
        assert config is not None
        command_id = args.command_id if args.command_id is not None else uuid4().hex
        validate_command_id(command_id)
        # A supplied command ID also makes an omitted task ID stable across CLI retries.
        task_id = args.task_id if args.task_id is not None else (
            uuid5(NAMESPACE_URL, f"diamond:create:{command_id}").hex if args.command_id is not None else uuid4().hex)
        role = Role(args.role)
        provider = config.resolve_provider(role, Provider(args.provider) if args.provider is not None else None)
        spec = TaskSpec(task_id, args.objective, role, provider)
        return {"task": store.create(spec, command_id).to_dict(), "command_id": command_id}, 0
    if args.task_command == "list":
        return {"tasks": [snapshot.to_dict() for snapshot in store.list()]}, 0
    if args.task_command == "show":
        return {"task": store.get(args.task_id).to_dict()}, 0
    if args.task_command == "attempts":
        return {"attempts": [attempt.to_dict() for attempt in store.attempts(args.task_id)]}, 0
    if args.task_command == "evidence":
        return {"evidence": [record.to_dict() for record in store.evidence(args.task_id)]}, 0
    if args.task_command == "claims":
        return {"claims": [claim.to_dict() for claim in store.dispatch_claims(args.task_id)]}, 0
    if args.task_command == "reconcile":
        return {"attempt": store.reconcile_attempt(args.attempt_id, args.command_id, args.reason).to_dict()}, 0
    if args.command == "shell":
        from .shell import DiamondShell
        shell = DiamondShell(args.project, args.config)
        return {"shell": True, "project": shell.project_root}, 0
    return {"events": [event.to_dict() for event in store.events(args.task_id)]}, 0


def _emit(result: dict[str, Any], exit_code: int, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps({"schema_version": 1, "ok": exit_code == 0, "result": result}, sort_keys=True))
    elif "providers" in result:
        for probe in result["providers"]:
            if "status" in probe:
                print(f"{probe['provider']}: {probe['status']} version={probe['version'] or 'UNKNOWN'} auth=NOT_CHECKED")
            else:
                print(f"{probe['provider']}: configured={probe.get('configured', False)} "
                      f"auth={probe.get('auth_status', 'NOT_CHECKED')}")
    elif "base_url" in result and "models" in result:
        print(f"{result['provider']}: models={len(result['models'])} "
              f"latency_ms={result.get('latency_ms', 'UNKNOWN')} live_request={result.get('live_request', False)}")
    elif "valid" in result:
        print(f"Configuration valid: {result['config']}")
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in arguments
    try:
        args = parser().parse_args(arguments)
        json_mode = getattr(args, "json", False)
        if args.command == "mcp":
            if not args.stdio:
                raise ValidationError("mcp requires --stdio")
            from .mcp_server import run_from_cli
            return run_from_cli(args.project, args.config, args.state_dir)
        if args.command == "shell":
            from .shell import main as shell_main
            return shell_main(args.project, args.config, args.state_dir)
        if args.command == "dashboard":
            from .dashboard import main as dashboard_main
            return dashboard_main(args.port)
        result, exit_code = _run(args)
        _emit(result, exit_code, json_mode)
        return exit_code
    except DiamondError as exc:
        if json_mode:
            print(json.dumps({"schema_version": 1, "ok": False,
                              "error": {"code": exc.code, "message": str(exc)}}, sort_keys=True))
        else:
            print(f"{exc.code}: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        if json_mode:
            print(json.dumps({"schema_version": 1, "ok": False,
                              "error": {"code": "INTERRUPTED", "message": "command interrupted"}}))
        else:
            print("INTERRUPTED: command interrupted", file=sys.stderr)
        return 130
    except Exception:
        if json_mode:
            print(json.dumps({"schema_version": 1, "ok": False,
                              "error": {"code": "INTERNAL_ERROR", "message": "unexpected internal error; report the command"}}))
        else:
            print("INTERNAL_ERROR: unexpected internal error; report the command", file=sys.stderr)
        return 1
