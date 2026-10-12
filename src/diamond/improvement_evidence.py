from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .daily_improve import EvidenceInput, analyze_evidence
from .errors import CommandConflictError, ConflictError, StorageError, ValidationError
from .evidence import EvidenceRecord, digest_text, redact_text
from .improvement_store import source_manifest
from .validation import bounded_int, canonical_json, command_id, parse_canonical_json, text, utc_now


WINDOW_QUERY = """
SELECT e.evidence_id,e.attempt_id,e.kind,e.created_at,e.digest_sha256,e.schema_version,
       length(CAST(e.payload_json AS BLOB)) AS payload_bytes,
       CASE WHEN length(CAST(e.payload_json AS BLOB)) <= ? THEN e.payload_json END AS payload_json,
       a.provider,a.role,a.workspace_root,a.task_id,a.cli_version,a.reported_model,
       we.workflow_id,de.delivery_id,cp.project_root AS component_project,
       dc.project_root AS delivery_project
FROM attempt_evidence e INDEXED BY evidence_window
JOIN attempts a ON a.attempt_id=e.attempt_id
LEFT JOIN workflow_events we ON we.event_type='STARTED' AND json_extract(we.payload_json,'$.attempt_id')=e.attempt_id
LEFT JOIN improvement_checkpoints cp ON cp.workflow_id=we.workflow_id
LEFT JOIN delivery_events de ON de.event_type='STARTED' AND json_extract(de.payload_json,'$.attempt_id')=e.attempt_id
LEFT JOIN deliveries d ON d.delivery_id=de.delivery_id
LEFT JOIN improvement_checkpoints dc ON dc.workflow_id=json_extract(json_extract(d.snapshot_json,'$.spec.components_json'),'$[0].workflow_id')
    AND json_array_length(json_extract(d.snapshot_json,'$.spec.components_json'))=1
WHERE e.created_at >= ? AND e.created_at <= ?
ORDER BY e.created_at DESC,e.evidence_id DESC LIMIT ?
"""


def analyze_snapshot(ledger, window_hours, max_candidates, *, project, max_records, max_bytes):
    bounded_int(window_hours, "daily improvement window hours", 1, 24 * 365)
    bounded_int(max_candidates, "daily improvement candidate limit", 1, 100)
    bounded_int(max_records, "evidence row limit", 1, 10000)
    bounded_int(max_bytes, "evidence byte limit", 1024, 32 * 1024 * 1024)
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    cutoff = (now - timedelta(hours=window_hours)).isoformat(timespec="microseconds").replace("+00:00", "Z")
    project = project.resolve(strict=True) if project is not None else None
    manifest = source_manifest(project) if project is not None else None
    source_digest = digest_text(manifest) if manifest is not None else None
    window = {"cutoff": cutoff, "watermark": timestamp, "max_records": max_records, "max_bytes": max_bytes,
              "rows_examined": 0, "payload_bytes": 0, "excluded_scope": 0, "truncated": False,
              "source_scope": str(project) if project else "UNKNOWN", "source_digest": source_digest,
              "evidence_watermark": None, "provider_context": [], "dispositions": []}
    records, contexts = [], set()
    if not ledger._database_present():
        return replace(analyze_evidence((), window_hours, max_candidates), read_window=window)
    with ledger._connection(write=False) as connection:
        cursor = connection.execute(WINDOW_QUERY, (min(max_bytes, 1024 * 1024), cutoff, timestamp, max_records + 1))
        for row in cursor:
            if window["rows_examined"] >= max_records or row["payload_json"] is None or window["payload_bytes"] + row["payload_bytes"] > max_bytes:
                window["truncated"] = True
                break
            window["rows_examined"] += 1
            window["payload_bytes"] += row["payload_bytes"]
            if window["evidence_watermark"] is None:
                window["evidence_watermark"] = [row["created_at"], row["evidence_id"]]
            try:
                checked = EvidenceRecord(row["evidence_id"], row["attempt_id"], row["kind"], row["created_at"],
                                         row["payload_json"], row["digest_sha256"], row["schema_version"])
            except (ValidationError, TypeError, ValueError) as exc:
                raise StorageError(f"invalid stored improvement evidence: {row['evidence_id']}") from exc
            scope = row["component_project"] or row["delivery_project"] or row["workspace_root"]
            if project is None or scope != str(project):
                window["excluded_scope"] += 1
                continue
            lineage = row["workflow_id"] or row["delivery_id"] or row["task_id"]
            payload = parse_canonical_json(checked.payload_json)
            records.append(EvidenceInput(checked.evidence_id, checked.attempt_id, checked.kind, checked.created_at,
                row["provider"], row["role"], payload, scope, lineage, payload.get("candidate_digest"),
                row["cli_version"], row["reported_model"]))
            contexts.add((row["provider"], row["cli_version"] or "UNKNOWN", row["reported_model"] or "UNKNOWN"))
        report = analyze_evidence(records, window_hours, 100)
        active, historical = [], list(report.historical_candidates)
        for candidate in report.candidates:
            disposition = connection.execute("SELECT status,reason,evidence_ids_json,expires_at FROM improvement_dispositions "
                "WHERE project_root=? AND source_digest=? AND signature=? AND created_at<=? "
                "ORDER BY created_at DESC,command_id DESC LIMIT 1",
                (str(project), source_digest, candidate.signature, timestamp)).fetchone()
            if disposition is not None and disposition["expires_at"] > timestamp:
                window["dispositions"].append({"signature": candidate.signature, "status": disposition["status"],
                    "reason": disposition["reason"], "evidence_ids": parse_canonical_json(disposition["evidence_ids_json"]),
                    "expires_at": disposition["expires_at"]})
                historical.append(replace(candidate, adjudication="operator_" + disposition["status"]))
            else:
                active.append(candidate)
    if project is not None and source_manifest(project) != manifest:
        raise ConflictError("source inputs changed during improvement analysis; retry against a stable snapshot")
    window["provider_context"] = [{"provider": provider, "cli_version": version, "reported_model": model}
                                  for provider, version, model in sorted(contexts)]
    return replace(report, generated_at=timestamp, candidates=tuple(active[:max_candidates]),
                   historical_candidates=tuple(historical[:max_candidates]), read_window=window)


def record_disposition(ledger, project: Path, signature: str, source_digest: str, status: str,
                       reason: str, evidence_ids: tuple[str, ...], key: str, cooldown_hours: int = 24) -> dict:
    command_id(key)
    text(reason, "candidate disposition reason", 4096)
    bounded_int(cooldown_hours, "disposition cooldown hours", 1, 24 * 365)
    if status not in {"resolved", "inapplicable", "failed_experiment", "deferred"}:
        raise ValidationError("unknown candidate disposition")
    if redact_text(reason) != reason:
        raise ValidationError("disposition reason contains credential-like text")
    if not evidence_ids or len(evidence_ids) > 1000 or len(set(evidence_ids)) != len(evidence_ids):
        raise ValidationError("disposition requires distinct supporting evidence IDs")
    ids = canonical_json(sorted(evidence_ids))
    request = (str(project), source_digest, signature, status, reason, ids)
    if ledger._database_present():
        with ledger._connection(write=False) as connection:
            prior = connection.execute("SELECT * FROM improvement_dispositions WHERE command_id=?", (key,)).fetchone()
            if prior is not None:
                fields = ("project_root", "source_digest", "signature", "status", "reason", "evidence_ids_json")
                if tuple(prior[field] for field in fields) != request:
                    raise CommandConflictError("disposition command ID was used for another decision")
                return dict(prior)
    report = analyze_snapshot(ledger, 168, 100, project=project, max_records=1000, max_bytes=8 * 1024 * 1024)
    if report.read_window["source_digest"] != source_digest:
        raise ConflictError("disposition source identity is stale")
    candidate = next((item for item in (*report.candidates, *report.historical_candidates) if item.signature == signature), None)
    if candidate is None or not set(evidence_ids) <= set(candidate.evidence_ids + candidate.resolution_evidence_ids):
        raise ValidationError("disposition must cite a current scoped candidate and its retained evidence")
    now = datetime.now(timezone.utc)
    created = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    expires = (now + timedelta(hours=cooldown_hours)).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with ledger._connection(write=True) as connection:
        connection.execute("INSERT INTO improvement_dispositions VALUES (?,?,?,?,?,?,?,?,?)", (key, *request, created, expires))
        return dict(connection.execute("SELECT * FROM improvement_dispositions WHERE command_id=?", (key,)).fetchone())
