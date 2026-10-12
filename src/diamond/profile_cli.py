import hashlib
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .agent_compiler import bind_profile
from .agent_profiles import ProfileCatalog, digest
from .domain import Provider, Role, TaskSpec
from .evidence import digest_text
from .errors import ValidationError
from .packets import OutputContract, TaskPacket
from .skill_catalog import load_skill_catalog
from .trial_design import load_trial_design, trial_template
from .trial_store import TrialAssignment, TrialStore
from .validation import canonical_json, task_id, utc_now
from .workflow import prompt
from .workflow_contracts import WorkflowStatus
from .workflow_store import WorkflowStore
from .workspace import workspace_manifest


def profile_command(args, paths, config, ledger):
    catalog = ProfileCatalog.load(paths.project)
    trials = TrialStore(ledger)
    if args.profile_command == "trial-template":
        return {"design": trial_template(catalog), "complete": False, "live_execution_authorized": False,
                "execution": {"provider_launch": False, "source_publication": False}}, 0
    if args.profile_command == "trial-validate":
        return load_trial_design(Path(args.file).expanduser().absolute(), catalog), 0
    if args.profile_command == "trial-register":
        report = load_trial_design(Path(args.file).expanduser().absolute(), catalog)
        registration = trials.register(report["design"], report["trial_design_digest"], args.command_id)
        return {"trial": registration.to_dict(), "status": "REGISTERED",
                "execution": {"provider_launch": False, "live_execution_authorized": False}}, 0
    if args.profile_command == "trial-list":
        result = []
        for registration in trials.list():
            result.append({"trial": registration.to_dict(),
                           "status": trials.status(registration.trial_id)["status"]})
        return {"trials": result, "execution": {"provider_launch": False, "live_execution_authorized": False}}, 0
    if args.profile_command == "trial-show":
        return trials.status(args.trial_id), 0
    if args.profile_command == "trial-cancel":
        result = trials.cancel(args.trial_id, args.reason, args.command_id)
        result["execution"] = {"provider_launch": False, "live_execution_authorized": False}
        return result, 0
    if args.profile_command == "trial-outcome":
        outcome = trials.record_outcome(args.assignment_id, args.status, args.provider_calls,
                                        args.wall_clock_seconds, args.cost_microunits, args.unknown_reason,
                                        args.command_id)
        return {"outcome": outcome.to_dict(),
                "execution": {"provider_launch": False, "live_execution_authorized": False}}, 0
    if args.profile_command == "trial-assign":
        registration = trials.get(args.trial_id)
        if trials.status(args.trial_id)["status"] != "REGISTERED":
            raise ValidationError("trial assignment requires an active registered trial")
        workflow = WorkflowStore(ledger).replay(args.workflow_id)
        if workflow.status is not WorkflowStatus.CREATED:
            raise ValidationError("trial assignment requires a not-yet-started workflow")
        if workflow.spec.schema_version != 2 or workflow.spec.trial_opt_in != args.trial_id:
            raise ValidationError("workflow must carry the explicit versioned opt-in for this trial")
        if workflow.spec.builder_provider is not Provider.CLAUDE:
            raise ValidationError("M2 trial assignment requires the fixed Claude Builder workflow route")
        if registration.catalog_digest != catalog.catalog_digest or set(registration.profile_ids) != {
                record.profile_id for record in catalog.records}:
            raise ValidationError("trial profile catalog is stale or differs from the current catalog")
        selection = catalog.select(Role.BUILDER, Provider.CLAUDE, workflow.spec.task_tags)
        if args.arm != "control" and args.arm != selection.profile_id:
            raise ValidationError("requested profile arm does not match the workflow's deterministic tag selection")
        profile = catalog.get(args.arm) if args.arm != "control" else None
        workflow_digest = digest_text(canonical_json(workflow.to_dict()))
        assignment_id = uuid5(NAMESPACE_URL, f"diamond:trial-assignment:{args.command_id}").hex
        created_at = utc_now()
        snapshot = {"schema_version": 1, "trial_id": args.trial_id,
                    "assignment_id": assignment_id, "created_at": created_at,
                    "trial_design_digest": registration.design_digest, "catalog_digest": catalog.catalog_digest,
                    "workflow_id": args.workflow_id, "workflow_revision": workflow.revision,
                    "workflow_digest": workflow_digest, "task_tags": list(workflow.spec.task_tags),
                    "lineage_key": args.lineage_key, "arm": args.arm,
                    "profile_id": profile.profile_id if profile else None,
                    "profile_version": profile.version if profile else None,
                    "profile_digest": profile.digest_sha256 if profile else None,
                    "selection_digest": selection.decision_digest,
                    "reserved_provider_calls": args.reserved_provider_calls,
                    "reserved_wall_clock_seconds": args.reserved_wall_clock_seconds}
        assignment_digest = digest_text(canonical_json(snapshot))
        assignment = TrialAssignment(assignment_id, args.trial_id, args.workflow_id, workflow.revision,
            workflow_digest, workflow.spec.task_tags, args.lineage_key, args.arm,
            profile.profile_id if profile else None, profile.version if profile else None,
            profile.digest_sha256 if profile else None, selection.decision_digest,
            args.reserved_provider_calls, args.reserved_wall_clock_seconds, created_at,
            args.command_id, assignment_digest)
        persisted = trials.assign(assignment, snapshot)
        return {"assignment": persisted.to_dict(), "snapshot": snapshot,
                "execution": {"provider_launch": False, "live_execution_authorized": False,
                              "dispatch": "not_implemented"}}, 0
    role = Role(args.role)
    provider = config.resolve_provider(role, Provider(args.provider) if args.provider else None)
    if role is not Role.BUILDER or provider is not Provider.CLAUDE or config.resolve_provider(role) is not provider:
        raise ValidationError("M2 preview requires the configured fixed Claude Builder route")
    workflow_id = args.workflow_id
    if workflow_id:
        if args.objective or args.workspace or args.task_id:
            raise ValidationError("workflow preview takes its objective, workspace and identity from the stored workflow")
        workflow = WorkflowStore(ledger).replay(workflow_id)
        if workflow.status is not WorkflowStatus.CREATED or workflow.spec.builder_provider is not provider:
            raise ValidationError("profile preview requires a not-yet-started Claude Builder workflow")
        root = Path(workflow.spec.workspace_root)
        base = workspace_manifest(root)
        if base != workflow.manifest_json:
            raise ValidationError("workflow workspace changed since acceptance")
        objective, timeout, corrections = workflow.spec.objective, workflow.spec.timeout_seconds, workflow.spec.correction_round_limit
        required_tests = tuple(test.name for test in workflow.spec.tests)
        base_prompt = prompt(workflow, None)
        plan_revision = workflow.revision
    else:
        if not args.workspace or not args.objective:
            raise ValidationError("profile preview needs --workflow-id or both --workspace and --objective")
        try:
            root = Path(args.workspace).expanduser().resolve(strict=True)
            base = workspace_manifest(root)
        except OSError as exc:
            raise ValidationError("profile preview workspace must exist") from exc
        objective, timeout, corrections = args.objective, config.controller.task_timeout_seconds, config.controller.correction_round_limit
        required_tests, plan_revision, base_prompt = (), 1, None
    config_digest = hashlib.sha256(paths.config.read_bytes()).hexdigest()
    if workflow_id and workflow.spec.config_digest != config_digest:
        raise ValidationError("workflow configuration changed since acceptance")
    skills = load_skill_catalog(root)
    selected_skills = skills.for_role(role)
    if base_prompt is None:
        base_prompt = ("Return a JSON object with files and summary. This preview grants no source ownership or tools.\n" +
            canonical_json({"objective": objective, "role": role.value, "owned_files": [], "required_tests": [],
                            "skill_guidance": list(skills.guidance_for(role))}))
    identifier = args.task_id or uuid5(NAMESPACE_URL, canonical_json(
        {"preview": workflow_id, "workspace": str(root), "objective": objective})).hex
    task_id(identifier)
    spec = TaskSpec(identifier, objective, role, provider, owned_paths=(),
        allowed_tools=(config.providers[provider].executable,), allowed_skills=tuple(item.name for item in selected_skills),
        required_tests=required_tests, timeout_seconds=timeout, correction_round_limit=corrections)
    packet = TaskPacket.from_task_spec(spec, uuid5(NAMESPACE_URL, f"profile-preview:{identifier}").hex, root,
        plan_revision, digest(base_prompt), digest(base), config_digest, skills.registry, (),
        OutputContract("json", ("files", "summary"), 131072))
    packet = replace(packet, provider_tools=())
    compiled = bind_profile(packet, base_prompt, catalog, tuple(args.tag))
    if workspace_manifest(root) != base:
        raise ValidationError("preview workspace changed while compiling")
    return {"catalog": str(catalog.catalog_path), "compiled": compiled.to_dict(), "packet": compiled.packet.to_dict(),
            "source_context": "accepted_workflow" if workflow_id else "objective_preview_without_source_ownership",
            "trial_design_fields": "profile trial-validate --file <operator-authored JSON>",
            "execution": {"provider_launch": False, "source_publication": False, "live_execution_authorized": False,
                          "requires_trial_design": True, "trial_dispatch": "not_implemented"}}, 0
