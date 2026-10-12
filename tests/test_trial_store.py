import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from diamond.agent_profiles import ProfileCatalog
from diamond.domain import Provider, Role
from diamond.errors import ConflictError, ExecutionGuardError, StorageError, ValidationError
from diamond.evidence import digest_text
from diamond.schema import MIGRATION_8_TO_9, SCHEMA_STATEMENTS, SCHEMA_VERSION
from diamond.store import EventStore
from diamond.trial_design import TEXT_FIELDS, validate_trial_design
from diamond.trial_store import TrialAssignment, TrialStore
from diamond.validation import canonical_json, utc_now
from diamond.workflow_contracts import TestCommand, WorkflowSpec
from diamond.workflow_store import WorkflowStore
from diamond.workspace import workspace_manifest
from support import ProjectCase


class TrialStoreTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        import shutil
        shutil.copytree(Path(__file__).resolve().parents[1] / "config/agents", self.project / "config/agents")
        self.workspace = (self.base / "trial-workspace").resolve()
        self.workspace.mkdir()
        (self.workspace / "source.py").write_text("value = 1\n")
        self.ledger = EventStore(self.project / ".diamond" / "diamond.sqlite3")
        self.catalog = ProfileCatalog.load(self.project)
        self.trials = TrialStore(self.ledger)
        with self.ledger._connection(write=True):
            pass

    def design(self, trial_id="m2-pilot-1", *, max_assignments=2, max_provider_calls=4,
               max_wall_clock_seconds=100, max_cost_microunits=100):
        value = {field: f"Documented fixture procedure for {field}" for field in TEXT_FIELDS}
        value.update(schema_version=1, trial_id=trial_id, role="builder", provider="claude",
                     catalog_digest=self.catalog.catalog_digest,
                     profile_ids=[item.profile_id for item in self.catalog.records], blinded_acceptance=True,
                     max_assignments=max_assignments, max_provider_calls=max_provider_calls,
                     max_wall_clock_seconds=max_wall_clock_seconds, max_cost_microunits=max_cost_microunits,
                     outcome_maturation_hours=24)
        return value

    def register(self, **kwargs):
        design = self.design(**kwargs)
        report = validate_trial_design(design, self.catalog)
        return self.trials.register(report["design"], report["trial_design_digest"], "trial-register-1")

    def workflow(self, trial_id: str):
        spec = WorkflowSpec("a" * 32, "Repair parser", str(self.workspace), ("source.py",),
            (TestCommand("smoke", (sys.executable, "-c", "assert True"), 5, 4096),),
            Provider.CLAUDE, Provider.CLAUDE, hashlib.sha256(
                (self.project / "config/diamond.toml").read_bytes()).hexdigest(),
            workspace_manifest(self.workspace), schema_version=2, task_tags=("parser", "python"),
            trial_opt_in=trial_id)
        return WorkflowStore(self.ledger).create(spec, "workflow-create-1")

    def assignment(self, trial_id, workflow_id="a" * 32, *, arm="builder-python-parser",
                   lineage="lineage-1", command="assignment-1", calls=2, seconds=50):
        tags = ("parser", "python")
        assignment_id = hashlib.sha256(f"{command}:assignment".encode()).hexdigest()[:32]
        created_at = utc_now()
        snapshot = {"schema_version": 1, "trial_id": trial_id, "assignment_id": assignment_id,
                    "created_at": created_at,
                    "trial_design_digest": self.trials.get(trial_id).design_digest,
                    "catalog_digest": self.catalog.catalog_digest, "workflow_id": workflow_id,
                    "workflow_revision": 1, "workflow_digest": "2" * 64, "task_tags": list(tags),
                    "lineage_key": lineage, "arm": arm,
                    "profile_id": arm if arm != "control" else None,
                    "profile_version": "1.0.0" if arm != "control" else None,
                    "profile_digest": "3" * 64 if arm != "control" else None,
                    "selection_digest": "4" * 64,
                    "reserved_provider_calls": calls, "reserved_wall_clock_seconds": seconds}
        return TrialAssignment(
            assignment_id, trial_id, workflow_id, 1,
            snapshot["workflow_digest"], tags, lineage, arm, snapshot["profile_id"], snapshot["profile_version"],
            snapshot["profile_digest"], snapshot["selection_digest"], calls, seconds, created_at, command,
            digest_text(canonical_json(snapshot))), snapshot

    def test_schema9_trial_tables_are_created_and_immutable(self):
        with closing(sqlite3.connect(self.ledger.database)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            for table in ("profile_trials", "profile_trial_events", "profile_trial_assignments", "profile_trial_outcomes"):
                self.assertIsNotNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

    def test_schema8_requires_explicit_trial_migration(self):
        old_database = self.base / "schema8.sqlite3"
        with closing(sqlite3.connect(old_database)) as connection, connection:
            for statement in SCHEMA_STATEMENTS[:-len(MIGRATION_8_TO_9)]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version=8")
        old_store = EventStore(old_database)
        with self.assertRaisesRegex(StorageError, "expected 9"):
            old_store.list()
        self.assertEqual(old_store.migrate(), SCHEMA_VERSION)
        with closing(sqlite3.connect(old_database)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 9)
            self.assertIsNotNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_trials'").fetchone())

    def test_registration_is_idempotent_and_cannot_be_replaced(self):
        registration = self.register()
        self.assertEqual(registration, self.trials.register(self.design(),
                         validate_trial_design(self.design(), self.catalog)["trial_design_digest"], "trial-register-1"))
        with closing(sqlite3.connect(self.ledger.database)) as connection, connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE profile_trials SET design_json='{}' WHERE trial_id=?", (registration.trial_id,))
        with self.assertRaises(ConflictError):
            changed = self.design(max_assignments=1)
            report = validate_trial_design(changed, self.catalog)
            self.trials.register(report["design"], report["trial_design_digest"], "trial-register-2")

    def test_assignment_reservations_and_append_only_retry_accounting(self):
        registration = self.register()
        self.workflow(registration.trial_id)
        first, first_snapshot = self.assignment(registration.trial_id)
        self.trials.assign(first, first_snapshot)
        second, second_snapshot = self.assignment(registration.trial_id, arm="control", lineage="lineage-2",
                                                  command="assignment-2")
        self.trials.assign(second, second_snapshot)
        with self.assertRaises(ExecutionGuardError):
            third, snapshot = self.assignment(registration.trial_id, lineage="lineage-3", command="assignment-3")
            self.trials.assign(third, snapshot)

        failed = self.trials.record_outcome(first.assignment_id, "FAILED", 1, 10, 7, None, "outcome-1")
        unknown = self.trials.record_outcome(first.assignment_id, "UNKNOWN", 1, 10, 3, "provider result missing", "outcome-2")
        succeeded = self.trials.record_outcome(first.assignment_id, "SUCCEEDED", 0, 0, 1, None, "outcome-3")
        self.assertEqual((failed.attempt_number, unknown.attempt_number, succeeded.attempt_number), (1, 2, 3))
        with self.assertRaises(ExecutionGuardError):
            self.trials.record_outcome(first.assignment_id, "FAILED", 0, 0, 0, None, "outcome-4")
        report = self.trials.status(registration.trial_id)
        self.assertEqual(report["accounting"]["reserved_provider_calls"], 4)
        self.assertEqual(report["accounting"]["used_provider_calls"], 2)
        self.assertEqual(report["accounting"]["used_cost_microunits"], 11)
        self.assertEqual(len(report["outcomes"]), 3)

    def test_stale_assignment_identity_and_cost_budget_fail_closed(self):
        registration = self.register(trial_id="m2-pilot-stale", max_cost_microunits=5)
        self.workflow(registration.trial_id)
        assignment, snapshot = self.assignment(registration.trial_id, command="assignment-stale")
        with self.assertRaises(ValidationError):
            self.trials.assign(assignment, {**snapshot, "catalog_digest": "0" * 64})
        self.trials.assign(assignment, snapshot)
        with self.assertRaises(ExecutionGuardError):
            self.trials.record_outcome(assignment.assignment_id, "SUCCEEDED", 1, 1, 6, None, "outcome-expensive")

    def test_cancel_fences_assignments_and_provider_outcomes(self):
        registration = self.register(trial_id="m2-pilot-cancel")
        self.trials.cancel(registration.trial_id, "operator stopped the preregistered pilot", "cancel-1")
        with self.assertRaises(ExecutionGuardError):
            assignment, snapshot = self.assignment(registration.trial_id, command="assignment-cancel")
            self.trials.assign(assignment, snapshot)

    def test_trial_opt_in_round_trip_is_versioned_and_ordinary_workflows_remain_schema1(self):
        registration = self.register(trial_id="m2-pilot-workflow")
        opted = self.workflow(registration.trial_id)
        self.assertEqual(opted.spec.schema_version, 2)
        self.assertEqual(opted.spec.trial_opt_in, registration.trial_id)
        self.assertEqual(opted.spec.task_tags, ("parser", "python"))
        self.assertEqual(WorkflowSpec.from_dict(opted.spec.to_dict()).to_dict(), opted.spec.to_dict())

    def test_cli_registers_assigns_and_accounts_without_provider_launch(self):
        design = self.design(trial_id="m2-cli-pilot")
        design_path = (self.project / "trial.json").resolve()
        design_path.write_text(json.dumps(design))
        registered = self.cli_process("--json", "profile", "trial-register", "--file", str(design_path),
                                      "--command-id", "cli-trial-register")
        self.assertEqual(registered.returncode, 0, registered.stdout + registered.stderr)
        workspace = self.workspace
        test_json = json.dumps({"name": "smoke", "argv": [sys.executable, "-c", "assert True"],
                                "timeout_seconds": 5, "output_limit": 4096})
        created = self.cli_process("--json", "workflow", "create", "--objective", "CLI trial workflow",
                                   "--workspace", str(workspace), "--owned-path", "source.py", "--test", test_json,
                                   "--task-tag", "parser", "--task-tag", "python", "--trial-opt-in", "m2-cli-pilot",
                                   "--command-id", "cli-workflow-create")
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        workflow_id = json.loads(created.stdout)["result"]["workflow"]["spec"]["workflow_id"]
        assigned = self.cli_process("--json", "profile", "trial-assign", "m2-cli-pilot", "--workflow-id", workflow_id,
                                    "--lineage-key", "cli-lineage", "--arm", "builder-python-parser",
                                    "--reserved-provider-calls", "2", "--reserved-wall-clock-seconds", "50",
                                    "--command-id", "cli-assignment")
        self.assertEqual(assigned.returncode, 0, assigned.stdout + assigned.stderr)
        assignment_id = json.loads(assigned.stdout)["result"]["assignment"]["assignment_id"]
        outcome = self.cli_process("--json", "profile", "trial-outcome", assignment_id, "--status", "UNKNOWN",
                                   "--provider-calls", "0", "--wall-clock-seconds", "0",
                                   "--unknown-reason", "fixture outcome only", "--command-id", "cli-outcome")
        self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
        status = self.cli_process("--json", "profile", "trial-show", "m2-cli-pilot")
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        report = json.loads(status.stdout)["result"]
        self.assertFalse(report["execution"]["provider_launch"])
        self.assertEqual(report["accounting"]["used_provider_calls"], 0)
