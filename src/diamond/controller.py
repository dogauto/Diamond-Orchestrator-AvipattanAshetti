import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .adapters import Invocation
from .evidence import (AttemptStatus, EvidenceRecord, ExecutionAttempt, digest_text,
                       environment_secrets, output_evidence, safe_excerpt)
from .errors import ConflictError, ValidationError
from .packets import ArtifactRef, PacketContext, TaskPacket
from .processes import ProcessIdentity
from .results import NormalizedResult, ParseStatus, parse_result
from .skills import SkillRegistry
from .store import EventStore
from .supervisor import CancelledExecution, ExecutionResult, ExecutionStatus, run
from .validation import bounded_int, canonical_json, command_id as validate_command_id, text, utc_now


async def run_persisted(store: EventStore, packet: TaskPacket, attempt: ExecutionAttempt,
                        invocation: Invocation, cwd: Path, owner: str, command_prefix: str,
                        registry: SkillRegistry, context: PacketContext,
                        available_capabilities: tuple[str, ...] = (),
                        timeout_seconds: float = 900,
                        output_limit: int = 1_048_576,
                        environment: dict[str, str] | None = None,
                        output_artifacts: tuple[ArtifactRef, ...] = (), max_workers: int = 2) -> tuple[ExecutionAttempt, NormalizedResult,
                                                                                  ExecutionResult, EvidenceRecord]:
    """Run one explicit invocation with launch intent and completion evidence in the ledger."""
    if not isinstance(store, EventStore) or not isinstance(packet, TaskPacket) or not isinstance(attempt, ExecutionAttempt):
        raise ValidationError("run_persisted requires a store, packet, and validated attempt")
    if not isinstance(invocation, Invocation):
        raise ValidationError("run_persisted requires a validated invocation")
    if not isinstance(cwd, Path):
        raise ValidationError("run_persisted cwd must be a pathlib.Path")
    if not isinstance(registry, SkillRegistry) or type(available_capabilities) is not tuple:
        raise ValidationError("run_persisted requires a skill registry and immutable capabilities")
    if not isinstance(context, PacketContext):
        raise ValidationError("run_persisted requires current accepted context")
    packet.verify_current(context.plan_revision, context.plan_digest, context.base_digest, context.config_digest)
    bounded_int(output_limit, "output_limit", 1, 16_777_216)
    bounded_int(max_workers, "max_workers", 1, 64)
    if type(output_artifacts) is not tuple or any(not isinstance(artifact, ArtifactRef) for artifact in output_artifacts):
        raise ValidationError("output artifacts must be an immutable tuple of ArtifactRef values")
    if environment is not None and (type(environment) is not dict or any(type(key) is not str or type(value) is not str
            for key, value in environment.items())):
        raise ValidationError("execution environment overrides must be a string mapping")
    if environment and any(key in environment for key in ("PATH", "HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")):
        raise ValidationError("execution overrides must not change executable resolution or provider state roots")
    packet.verify(registry, available_capabilities)
    packet.authorize_invocation(invocation, cwd)
    if (attempt.task_id, attempt.task_revision, attempt.attempt_id, attempt.role, attempt.provider,
            attempt.requested_model, attempt.workspace_root, attempt.executable) != \
            (packet.task_id, packet.task_revision, packet.attempt_id, packet.role, packet.provider,
             packet.requested_model, packet.workspace_root, invocation.argv[0]):
        raise ValidationError("packet, attempt, and invocation launch identities do not match")
    secrets = environment_secrets(environment)
    text(command_prefix, "attempt command prefix", 80)
    validate_command_id(f"{command_prefix}:intent")
    validate_command_id(f"{command_prefix}:claim")
    validate_command_id(f"{command_prefix}:start")
    validate_command_id(f"{command_prefix}:complete")
    validate_command_id(f"{command_prefix}:evidence")

    claim, intent = store.reserve_dispatch(packet, attempt, owner, f"{command_prefix}:claim", max_workers)
    if intent.status is not AttemptStatus.INTENDED:
        raise ConflictError("execution attempt already advanced; refusing blind relaunch")
    launch_payload = canonical_json({"argv": [safe_excerpt(arg, 4096, secrets) for arg in invocation.argv],
        "argv_digest": digest_text(canonical_json(list(invocation.argv))), "cwd": str(cwd.resolve()),
        "packet_digest": packet.packet_digest(), "host_write_enforced": True,
        "provider_tools": list(packet.provider_tools) if packet.provider_tools is not None else None})
    store.record_evidence(EvidenceRecord(uuid4().hex, intent.attempt_id, "launch_intent", utc_now(),
                                         launch_payload, digest_text(launch_payload)), f"{command_prefix}:launch")
    packet.verify(registry, available_capabilities)
    started = store.acquire_start(claim, f"{command_prefix}:start")

    def process_started(pid: int, identity: ProcessIdentity | None) -> None:
        payload = canonical_json({"pid": pid, "identity": identity.to_dict() if identity is not None else None})
        store.record_evidence(EvidenceRecord(uuid4().hex, started.attempt_id, "process_started", utc_now(),
                                             payload, digest_text(payload)), f"{command_prefix}:process")

    cancelled = False
    try:
        execution = await run(invocation, cwd, min(timeout_seconds, packet.timeout_seconds), output_limit, environment,
                              on_started=process_started, enforce_paths=True,
                              owned_paths=packet.owned_paths if invocation.permission_mode.value != "plan" else ())
    except CancelledExecution as exc:
        execution, cancelled = exc.result, True
    except asyncio.CancelledError:
        store.reconcile_attempt(started.attempt_id, f"{command_prefix}:cancel", "launch interrupted; process state unproven")
        raise
    except Exception as exc:
        store.reconcile_attempt(started.attempt_id, f"{command_prefix}:exception",
                                safe_excerpt(f"controller exception; reconciliation required: {exc}", 4096, secrets))
        raise

    try:
        result = parse_result(invocation, execution)
    except (ValidationError, ValueError, RecursionError):
        result = NormalizedResult(ParseStatus.MALFORMED, None, None, None, (), "provider result failed strict normalization")
    artifact_error = None
    try:
        packet.verify_output_artifacts(output_artifacts)
        if result.status is ParseStatus.VALID:
            packet.output_contract.verify(result.structured_output or result.content)
    except ValidationError as exc:
        artifact_error = str(exc)
    if execution.status is ExecutionStatus.CLEANUP_FAILED:
        status = AttemptStatus.AMBIGUOUS
        detail = "process cleanup could not be proven; reconciliation required"
    elif cancelled:
        status, detail = AttemptStatus.CANCELLED, "execution cancelled; supervised process group cleaned"
    elif artifact_error is not None:
        status = AttemptStatus.FAILED
        detail = f"output artifact validation failed: {artifact_error}"
    elif execution.status is ExecutionStatus.SUCCEEDED and result.status is ParseStatus.VALID:
        status = AttemptStatus.SUCCEEDED
        detail = result.detail
    else:
        status = AttemptStatus.FAILED
        detail = result.detail or execution.detail or execution.status.value
    completed = replace(
        started,
        status=status,
        completed_at=utc_now(),
        provider_session_id=safe_excerpt(result.session_id, 4096, secrets) if result.session_id else None,
        reported_model=safe_excerpt(result.reported_model, 256, secrets) if result.reported_model else None,
        returncode=execution.returncode,
        signal=-execution.returncode if execution.returncode is not None and execution.returncode < 0 else None,
        stdout_bytes=execution.stdout_bytes if execution.stdout_bytes is not None else len(execution.stdout.encode("utf-8")),
        stderr_bytes=execution.stderr_bytes if execution.stderr_bytes is not None else len(execution.stderr.encode("utf-8")),
        stdout_sha256=execution.stdout_sha256 or digest_text(execution.stdout),
        stderr_sha256=execution.stderr_sha256 or digest_text(execution.stderr),
        result_status=result.status.value,
        detail=safe_excerpt(detail, 4096, secrets),
        revision=started.revision + 1,
    )
    evidence = output_evidence(completed, invocation, execution, result, utc_now(), output_artifacts, secrets, artifact_error)
    try:
        completed = store.complete_attempt(completed, started.revision, evidence, f"{command_prefix}:complete")
    except ConflictError:
        store.record_evidence(replace(evidence, kind="late_result"), f"{command_prefix}:late")
        raise
    if cancelled:
        raise asyncio.CancelledError
    return completed, result, execution, evidence
