import ast
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .adapters import Invocation, OutputMode, PermissionMode, build_invocation
from .config import DiamondConfig
from .controller import run_persisted
from .diagnostics import probe_version
from .domain import Provider, Role, TaskSpec
from .errors import ConflictError, ValidationError
from .evidence import AttemptStatus, EvidenceRecord, ExecutionAttempt, digest_text, safe_excerpt
from .packets import ArtifactRef, OutputContract, PacketContext, TaskPacket
from .skills import SkillRegistry
from .store import EventStore
from .supervisor import ExecutionStatus, run
from .validation import canonical_json, utc_now


_CODE_BRIEF = """Return only a raw JSON object with a 'code' field containing Python 3.11 source.
Implement parse_duration(value: str) -> int. Accept only ASCII digits with the exact syntax
<minutes>m<seconds>s, no whitespace, signs, trailing newline or extra text. Minutes must be
0..1440 and seconds 0..59. Return total seconds. For a non-string raise TypeError;
for bad syntax or an out-of-range number raise ValueError. Leading zeroes are allowed.
Only define parse_duration and optionally import re. No other imports, top-level actions,
files, network or tool calls. Do not use markdown fences."""

_CODE_CHECKS = """import json
from src.duration import parse_duration
cases = [('0m0s',0),('1m2s',62),('0m59s',59),('1440m59s',86459),('001m02s',62)]
passed = 0
for value, expected in cases:
    assert parse_duration(value) == expected, repr(value)
    passed += 1
for value in ['', '1m60s', '1441m0s', '-1m2s', '1m-2s', '1m2s\\n', ' 1m2s',
              '1m2s ', '1m2sother', '1.0m2s', '1m', '１２m2s', '1m２s']:
    try: parse_duration(value)
    except ValueError: passed += 1
    else: raise AssertionError(repr(value))
for value in [None, True, 62, ['1m2s']]:
    try: parse_duration(value)
    except TypeError: passed += 1
    else: raise AssertionError(repr(value))
print(json.dumps({'passed': passed, 'status': 'PASS'}, sort_keys=True))
"""


def candidate_manifest(project: Path) -> dict[str, str]:
    files = (*project.glob("src/diamond/*.py"), *project.glob("tests/*.py"),
             *project.glob("scripts/*.sh"), *project.glob("scripts/*.py"), project / "config" / "diamond.toml")
    return {str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)}


def validate_code(source: object) -> str:
    if not isinstance(source, str) or not source.strip() or len(source.encode()) > 32768:
        raise ValidationError("code response must be bounded Python source")
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError) as exc:
        raise ValidationError("code response has invalid Python syntax") from exc
    functions = [item for item in tree.body if isinstance(item, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != "parse_duration":
        raise ValidationError("code response must define only parse_duration")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if not isinstance(node, ast.Import) or len(node.names) != 1 or node.names[0].name != "re" or node.names[0].asname:
                raise ValidationError("code-response fixture allows only import re")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValidationError("code-response fixture disallows private attributes")
        if isinstance(node, ast.Name) and node.id in {"eval", "exec", "open", "compile", "__import__", "globals", "locals", "getattr", "setattr"}:
            raise ValidationError("code-response fixture contains an unsupported operation")
    if any(not isinstance(item, (ast.Import, ast.FunctionDef)) for item in tree.body) or functions[0].decorator_list:
        raise ValidationError("code-response fixture disallows executable top-level actions")
    return source


def _record(store: EventStore, attempt_id: str, kind: str, payload: dict[str, object]) -> EvidenceRecord:
    value = canonical_json(payload)
    record = EvidenceRecord(uuid4().hex, attempt_id, kind, utc_now(), value, digest_text(value))
    return store.record_evidence(record, f"{kind}:{attempt_id}")


def _restart_read(project: Path, store: EventStore, attempt: ExecutionAttempt) -> bool:
    env = dict(os.environ, PYTHONPATH=str(project / "src"), PYTHONDONTWRITEBYTECODE="1")
    response = subprocess.run([sys.executable, "-B", "-m", "diamond", "--project", str(project),
        "--state-dir", str(store.database.parent), "--json", "task", "attempts", attempt.task_id],
        capture_output=True, text=True, timeout=10, env=env)
    return response.returncode == 0 and json.loads(response.stdout)["result"]["attempts"] == [attempt.to_dict()]


async def smoke(project: Path, config: DiamondConfig, config_path: Path, store: EventStore,
                providers: tuple[Provider, ...], code_test: bool = False) -> dict[str, object]:
    if not providers or len(set(providers)) != len(providers):
        raise ValidationError("smoke requires distinct explicitly selected providers")
    if Provider.GEMINI in providers:
        raise ValidationError("Gemini confirmation is blocked by the recorded daily quota; select another provider until reset is confirmed")
    if code_test and Provider.CLAUDE not in providers:
        raise ValidationError("the code-response fixture requires an explicitly selected Claude provider")
    manifest = candidate_manifest(project)
    candidate = digest_text(canonical_json(manifest))
    outcomes: list[dict[str, object]] = []
    registry = SkillRegistry(())
    parent = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    if not parent.is_dir():
        raise ValidationError("smoke temporary parent must exist")
    operations = [(provider, "marker") for provider in providers]
    if code_test:
        operations.append((Provider.CLAUDE, "code-response"))
    for provider, operation in operations:
        config.resolve_provider(Role.QA, provider)
        diagnostic = probe_version(provider, config.providers[provider].executable)
        if diagnostic.status != "available" or diagnostic.executable is None:
            outcomes.append({"provider": provider.value, "operation": operation, "status": "BLOCKED",
                             "reason": diagnostic.status})
            continue
        with tempfile.TemporaryDirectory(prefix="diamond-real-", dir=parent) as directory:
            workspace = Path(directory).resolve()
            marker = f"DIAMOND_PERSISTED_{provider.value.upper()}_OK"
            objective = _CODE_BRIEF if operation == "code-response" else (
                f"Reply with exactly {marker}. Do not use tools or change any files.")
            spec = TaskSpec(uuid4().hex, objective, Role.BUILDER if operation == "code-response" else Role.QA,
                            provider, owned_paths=("src/duration.py",) if operation == "code-response" else (),
                            allowed_tools=(diagnostic.executable,), timeout_seconds=180)
            store.create(spec, f"create:{spec.task_id}")
            context = PacketContext(1, digest_text(objective), digest_text("empty isolated workspace"),
                                    hashlib.sha256(config_path.read_bytes()).hexdigest())
            output = OutputContract("json", ("code",), 32768) if operation == "code-response" else OutputContract("text", (), 4096)
            packet = TaskPacket.from_task_spec(spec, uuid4().hex, workspace, 1, context.plan_digest, context.base_digest,
                                               context.config_digest, registry, (), output)
            if provider is Provider.CLAUDE:
                packet = replace(packet, provider_tools=())
            invocation = build_invocation(provider, diagnostic.executable, objective,
                stream=provider is Provider.ANTIGRAVITY, provider_tools=packet.provider_tools,
                permission_mode=PermissionMode.DEFAULT if provider is Provider.CLAUDE else PermissionMode.PLAN,
                json_schema='{"type":"object","properties":{"code":{"type":"string"}},"required":["code"],"additionalProperties":false}'
                            if operation == "code-response" else None)
            attempt = ExecutionAttempt(packet.attempt_id, spec.task_id, 1, spec.role, provider, diagnostic.executable,
                                       packet.workspace_root, AttemptStatus.INTENDED, utc_now(), cli_version=diagnostic.version)
            prefix = f"real:{attempt.attempt_id}"
            completed, result, execution, evidence = await run_persisted(store, packet, attempt, invocation, workspace,
                "explicit-live-test", prefix, registry, context, timeout_seconds=180, max_workers=config.controller.max_workers)
            duplicate_rejected = False
            try:
                await run_persisted(store, packet, attempt, invocation, workspace, "explicit-live-test", prefix, registry, context)
            except ConflictError:
                duplicate_rejected = True
            observed_paths = sorted(str(path.relative_to(workspace)) for path in workspace.rglob("*"))
            workspace_unchanged = not observed_paths
            restart_verified = _restart_read(project, store, completed)
            accepted = completed.status is AttemptStatus.SUCCEEDED and duplicate_rejected and restart_verified and workspace_unchanged
            details: dict[str, object] = {}
            if operation == "marker":
                accepted = accepted and result.content is not None and result.content.strip() == marker
                details["expected_marker"] = marker
                details["marker_matches"] = result.content is not None and result.content.strip() == marker
            elif accepted:
                try:
                    source = validate_code(json.loads(result.structured_output or result.content)["code"])
                    (workspace / "src").mkdir()
                    target = workspace / "src" / "duration.py"
                    target.write_text(source, encoding="utf-8")
                    artifact = ArtifactRef("duration utility", "src/duration.py", hashlib.sha256(target.read_bytes()).hexdigest(),
                                           target.stat().st_size, "source")
                    packet.verify_output_artifacts((artifact,))
                    checked = await run(Invocation(Provider.CLAUDE, (sys.executable, "-B", "-c", _CODE_CHECKS), OutputMode.TEXT,
                        None, False, PermissionMode.DEFAULT), workspace, timeout_seconds=10, output_limit=8192,
                        environment={"PYTHONDONTWRITEBYTECODE": "1"}, enforce_paths=True)
                    passed = json.loads(checked.stdout) if checked.status is ExecutionStatus.SUCCEEDED else {}
                    accepted = passed == {"passed": 22, "status": "PASS"}
                    details.update(artifact=artifact.to_dict(), checks=passed, test_returncode=checked.returncode,
                                   test_stdout_sha256=checked.stdout_sha256, test_stderr=safe_excerpt(checked.stderr, 4096))
                    if accepted:
                        durable = store.database.parent / f"duration-{attempt.attempt_id}.py"
                        durable.write_text(source, encoding="utf-8")
                        details["retained_artifact"] = str(durable)
                except (ValidationError, ValueError, KeyError, OSError) as exc:
                    accepted = False
                    details["code_error"] = safe_excerpt(str(exc), 4096)
            receipt = {"candidate_sha256": candidate, "provider": provider.value, "operation": operation,
                "task_id": spec.task_id, "attempt_id": attempt.attempt_id, "status": "PASS" if accepted else "FAIL",
                "attempt_status": completed.status.value, "returncode": execution.returncode,
                "cli_version": diagnostic.version, "session_id": completed.provider_session_id,
                "reported_model": completed.reported_model or "UNKNOWN", "duplicate_rejected": duplicate_rejected,
                "restart_verified": restart_verified, "workspace_unchanged": workspace_unchanged,
                "workspace_paths": observed_paths,
                "host_write_enforced": True, "evidence_sha256": evidence.digest_sha256, **details}
            _record(store, attempt.attempt_id, "live_acceptance", receipt)
            outcomes.append(receipt)
    if candidate_manifest(project) != manifest:
        raise ValidationError("source candidate changed during live verification; acceptance is stale")
    report = {"candidate_sha256": candidate, "source_manifest": manifest, "database": str(store.database),
              "outcomes": outcomes, "gemini": "QUOTA_BLOCKED_NOT_INVOKED",
              "ok": all(item["status"] == "PASS" for item in outcomes)}
    if store.database.parent.is_dir():
        path = store.database.parent / f"smoke-{uuid4().hex}.json"
        path.write_text(canonical_json(report) + "\n", encoding="utf-8")
        report["report"] = str(path)
    return report
