from dataclasses import replace

from .domain import LedgerEvent, TaskSnapshot, TaskSpec, TaskStatus, validate_transition
from .errors import StorageError, ValidationError
from .validation import exact_keys, parse_canonical_json


def next_snapshot(current: TaskSnapshot, target: TaskStatus, reason: str | None, timestamp: str) -> TaskSnapshot:
    validate_transition(current, target, reason)
    return replace(current, status=target, revision=current.revision + 1, updated_at=timestamp,
                   blocking_reason=reason if target is TaskStatus.BLOCKED else None,
                   resume_status=TaskStatus.CREATED if target is TaskStatus.BLOCKED else None)


def replay_events(events: tuple[LedgerEvent, ...]) -> TaskSnapshot:
    if not events:
        raise StorageError("task has no creation event; ledger is inconsistent")
    current: TaskSnapshot | None = None
    try:
        for index, event in enumerate(events, 1):
            if event.revision != index or event.task_id != events[0].task_id:
                raise StorageError("event stream has a task/revision gap")
            payload = parse_canonical_json(event.payload_json)
            if event.event_type == "TASK_CREATED" and index == 1:
                data = exact_keys(payload, {"schema_version", "spec"}, "creation payload")
                if type(data["schema_version"]) is not int or data["schema_version"] != 1:
                    raise StorageError("unsupported creation payload schema")
                spec = TaskSpec.from_dict(data["spec"])
                if spec.task_id != event.task_id:
                    raise StorageError("creation event task ID does not match its spec")
                current = TaskSnapshot(spec, TaskStatus.CREATED, 1, event.timestamp, event.timestamp)
            elif event.event_type == "TASK_TRANSITIONED" and current is not None:
                data = exact_keys(payload, {"schema_version", "from_status", "to_status", "reason", "resume_status"}, "transition payload")
                if type(data["schema_version"]) is not int or data["schema_version"] != 1:
                    raise StorageError("unsupported transition payload schema")
                if data["from_status"] != current.status.value:
                    raise StorageError("transition source disagrees with event history")
                target = TaskStatus(data["to_status"])
                current = next_snapshot(current, target, data["reason"], event.timestamp)
                if data["resume_status"] != (current.resume_status.value if current.resume_status else None):
                    raise StorageError("transition resume state disagrees with event history")
            else:
                raise StorageError("event stream must begin with exactly one creation event")
        assert current is not None
        return current
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        raise StorageError(f"invalid event stream: {exc}") from exc
