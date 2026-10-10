import sqlite3
from pathlib import Path

from .delivery_contracts import DeliverySnapshot, DeliveryStatus, RUNNING
from .errors import ConflictError
from .validation import parse_canonical_json


def occupied_deliveries(connection: sqlite3.Connection) -> list[dict]:
    terminal = "('ACCEPTED','REJECTED','STALE')"
    return [parse_canonical_json(row[0]) for row in connection.execute(
        f"SELECT snapshot_json FROM deliveries WHERE status NOT IN {terminal}")]


def protect_delivery_roots(connection: sqlite3.Connection, paths: tuple[Path, ...], exclude: str | None = None) -> None:
    for snapshot in occupied_deliveries(connection):
        if snapshot["spec"]["delivery_id"] == exclude:
            continue
        root = Path(snapshot["spec"]["workspace_root"])
        if any(path.is_relative_to(root) or root.is_relative_to(path) for path in paths):
            raise ConflictError("workspace overlaps a frozen delivery candidate")


def delivery_occupancy(connection: sqlite3.Connection, current: DeliverySnapshot) -> None:
    root = Path(current.spec.workspace_root)
    protect_delivery_roots(connection, (root,), current.spec.delivery_id)
    protect_workspace_writers(connection, root)
    count, _ = worker_occupancy(connection)
    if count >= current.spec.max_workers:
        raise ConflictError("delivery worker capacity is occupied")


def protect_workspace_writers(connection: sqlite3.Connection, root: Path) -> None:
    for row in connection.execute("SELECT snapshot_json FROM workflows WHERE status IN ('BUILDING','DEBUGGING','BLOCKED')"):
        spec = parse_canonical_json(row[0])["spec"]
        if any((Path(spec["workspace_root"]) / path).is_relative_to(root) or root.is_relative_to(Path(spec["workspace_root"]) / path)
               for path in spec["owned_paths"]):
            raise ConflictError("delivery overlaps an active or quarantined workflow")
    for row in connection.execute("SELECT packet_json FROM dispatch_claims JOIN attempts USING(attempt_id) WHERE status IN ('INTENDED','STARTED','AMBIGUOUS')"):
        packet = parse_canonical_json(row[0])
        if any((Path(packet["workspace_root"]) / path).is_relative_to(root) or root.is_relative_to(Path(packet["workspace_root"]) / path)
               for path in packet["owned_paths"]):
            raise ConflictError("delivery overlaps an unresolved writing attempt")


def worker_occupancy(connection: sqlite3.Connection) -> tuple[int, set[str]]:
    reserved = []
    for row in connection.execute("SELECT snapshot_json FROM workflows WHERE status IN ('BUILDING','DEBUGGING','BLOCKED')"):
        reserved.append(parse_canonical_json(row[0]))
    for snapshot in occupied_deliveries(connection):
        if snapshot["status"] in {status.value for status in RUNNING} | {DeliveryStatus.BLOCKED.value}:
            reserved.append(snapshot)
    covered = {item["active_attempt_id"] for item in reserved if item["active_attempt_id"] is not None}
    extra = sum(row[0] not in covered for row in connection.execute("SELECT attempt_id FROM attempts WHERE status IN ('INTENDED','STARTED','AMBIGUOUS')"))
    return len(reserved) + extra, covered
