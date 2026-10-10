import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .config import DiamondConfig
from .delivery import run_stage
from .delivery_contracts import DeliverySpec, DeliveryStatus, ROLES, next_role
from .delivery_store import DeliveryStore
from .diagnostics import probe_version
from .errors import ValidationError
from .integration import capture_components
from .store import EventStore
from .validation import canonical_json
from .workflow_cli import protect_inputs
from .workflow_contracts import TestCommand
from .workspace import workspace_manifest


def delivery_command(args: argparse.Namespace, config_path: Path, config: DiamondConfig,
                     ledger: EventStore) -> tuple[dict[str, Any], int]:
    deliveries = DeliveryStore(ledger)
    digest = hashlib.sha256(config_path.read_bytes()).hexdigest()
    if args.delivery_command == "create":
        identifier = args.delivery_id or uuid5(NAMESPACE_URL, f"diamond:delivery-create:{args.command_id}").hex
        try:
            root = Path(args.workspace).expanduser().resolve(strict=True)
            refs = tuple((value.split(":")[0], int(value.split(":")[1])) for value in args.component)
            tests = tuple(TestCommand.from_dict(json.loads(value)) for value in args.test)
            acceptance = canonical_json(json.loads(args.requirements))
        except (OSError, ValueError, TypeError, IndexError, RecursionError) as exc:
            raise ValidationError("delivery needs an existing target, workflow_id:revision components and trusted test/requirement JSON") from exc
        manifest = workspace_manifest(root)
        components = capture_components(ledger, refs, root, manifest)
        spec = DeliverySpec(identifier, args.objective, str(root), manifest, components, tests, acceptance,
                            tuple(config.resolve_provider(role) for role in ROLES), digest, supersedes=args.supersedes,
                            timeout_seconds=min(config.controller.task_timeout_seconds, 900), max_workers=config.controller.max_workers)
        protect_inputs(spec.work_spec(), config_path, config)
        return {"delivery": deliveries.create(spec, args.command_id).to_dict()}, 0
    if args.delivery_command == "list":
        return {"deliveries": [item.to_dict() for item in deliveries.list()]}, 0
    if args.delivery_command == "show":
        return {"delivery": deliveries.replay(args.delivery_id).to_dict(), "evidence_scope": "historical_candidate_snapshot"}, 0
    if args.delivery_command == "validate":
        current = deliveries.validate(args.delivery_id, digest)
        return {"delivery": current.to_dict()}, 0 if current.status is DeliveryStatus.ACCEPTED else 1
    if args.delivery_command == "certificate":
        return {"certificate": deliveries.certificate(args.delivery_id, digest)}, 0
    current = deliveries.get(args.delivery_id)
    if args.delivery_command == "reconcile":
        if current.revision != args.expected_revision:
            from .errors import ConflictError
            raise ConflictError("delivery reconciliation revision is stale")
        return {"delivery": deliveries.invalidate(current, args.reason).to_dict()}, 1
    role = next_role(current.status)
    provider = config.resolve_provider(role, current.spec.provider(role))
    if provider.value == "gemini":
        raise ValidationError("Gemini remains quota-blocked; no probe or stage invocation is authorized")
    diagnostic = probe_version(provider, config.providers[provider].executable)
    if diagnostic.status != "available" or diagnostic.executable is None:
        raise ValidationError(f"delivery provider is unavailable: {diagnostic.status}")
    protect_inputs(current.spec.work_spec(), config_path, config)
    result = asyncio.run(run_stage(ledger, args.delivery_id, args.expected_revision, digest, diagnostic.executable, diagnostic.version))
    return {"delivery": result.to_dict()}, 0 if result.status in {DeliveryStatus.INTEGRATED, DeliveryStatus.QA_PASSED,
        DeliveryStatus.REVIEW_PASSED, DeliveryStatus.ACCEPTED} else 1
