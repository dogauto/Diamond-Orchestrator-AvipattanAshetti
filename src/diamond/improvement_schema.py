STATEMENTS = (
    """CREATE TABLE improvement_cancellations (
        workflow_id TEXT PRIMARY KEY REFERENCES improvement_checkpoints(workflow_id),
        timestamp TEXT NOT NULL, reason TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TABLE improvement_checkpoints (
        workflow_id TEXT PRIMARY KEY REFERENCES workflows(workflow_id), project_root TEXT NOT NULL,
        base_manifest_json TEXT NOT NULL, config_digest TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TABLE improvement_publications (
        workflow_id TEXT PRIMARY KEY REFERENCES improvement_checkpoints(workflow_id),
        project_root TEXT NOT NULL, delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
        intent_json TEXT NOT NULL, digest_sha256 TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE TABLE improvement_publication_events (
        workflow_id TEXT NOT NULL REFERENCES improvement_publications(workflow_id),
        revision INTEGER NOT NULL CHECK(revision >= 1), status TEXT NOT NULL
            CHECK(status IN ('PUBLISHING','PUBLISHED','ROLLING_BACK','ROLLED_BACK','BLOCKED')),
        timestamp TEXT NOT NULL, detail TEXT NOT NULL,
        PRIMARY KEY(workflow_id,revision)) WITHOUT ROWID""",
    """CREATE INDEX evidence_window ON attempt_evidence(created_at,evidence_id)""",
    """CREATE INDEX evidence_attempt ON attempt_evidence(attempt_id,created_at,evidence_id)""",
    """CREATE INDEX workflow_attempt_lineage ON workflow_events(json_extract(payload_json,'$.attempt_id'))
        WHERE event_type='STARTED'""",
    """CREATE INDEX delivery_attempt_lineage ON delivery_events(json_extract(payload_json,'$.attempt_id'))
        WHERE event_type='STARTED'""",
    """CREATE TABLE improvement_dispositions (
        command_id TEXT PRIMARY KEY, project_root TEXT NOT NULL, source_digest TEXT NOT NULL,
        signature TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('resolved','inapplicable','failed_experiment','deferred')),
        reason TEXT NOT NULL, evidence_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL) WITHOUT ROWID""",
    """CREATE INDEX disposition_scope ON improvement_dispositions(project_root,source_digest,signature,created_at)""",
) + tuple(
    statement
    for table, identity in (
        ("improvement_cancellations", "workflow_id=NEW.workflow_id"),
        ("improvement_dispositions", "command_id=NEW.command_id"),
        ("improvement_checkpoints", "workflow_id=NEW.workflow_id"),
        ("improvement_publications", "workflow_id=NEW.workflow_id"),
        ("improvement_publication_events", "workflow_id=NEW.workflow_id AND revision=NEW.revision"),
    )
    for statement in (
        f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END",
        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END",
        f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS (SELECT 1 FROM {table} WHERE {identity}) "
        f"BEGIN SELECT RAISE(ABORT, '{table} cannot be replaced'); END",
    )
)
