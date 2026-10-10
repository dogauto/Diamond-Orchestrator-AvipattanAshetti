import asyncio
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from unittest.mock import patch

from diamond.domain import Provider
from diamond.errors import CommandConflictError, ConflictError, ExecutionGuardError, StorageError, ValidationError
from diamond.evidence import digest_text
from diamond.supervisor import ExecutionResult, ExecutionStatus
from diamond.workflow_cli import protect_inputs
from diamond.config import load_config
from diamond.store import EventStore
from diamond.validation import canonical_json
from diamond.workflow import run_step
from diamond.workflow_contracts import TestCommand, WorkflowSpec, WorkflowStatus
from diamond.workflow_store import WorkflowStore
from diamond.workspace import apply_response, workspace_manifest
from support import ProjectCase, TASK_ID


class WorkflowTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.ledger = EventStore(self.base.resolve() / "state" / "diamond.sqlite3")
        self.workflows = WorkflowStore(self.ledger)
        (self.project / "src").mkdir()
        (self.project / "tests").mkdir()
        (self.project / "tests" / "check.py").write_text("import sys\nsys.path.insert(0,'.')\nfrom src.value import value\nassert value()==42\nprint('verified')\n")
        self.spec = WorkflowSpec(TASK_ID, "implement value() returning 42", str(self.project.resolve()), ("src/value.py",),
            (TestCommand("value regression", (sys.executable, "-B", "tests/check.py")),), Provider.CLAUDE, Provider.CODEX,
            "f" * 64, workspace_manifest(self.project.resolve()), timeout_seconds=10)

    def provider(self, contents: tuple[str, ...], *, extra_path: str | None = None, bad_citation: bool = False,
                 require_failure_tail: bool = False):
        encoded = json.dumps(contents)
        body = f"""import json,sys
prompt=sys.argv[-1]
data=json.loads(prompt.split('\\n',1)[1])
debug='failure_packet' in data
content=json.loads({encoded!r})[1 if debug else 0]
file=data['owned_files'][0]
response={{'files':[{{'relative_path':{extra_path!r} or file['relative_path'],'base_sha256':file['base_sha256'],'content':content}}],'summary':'bounded fixture'}}
if debug:
  if {require_failure_tail!r}: assert 'AssertionError' in data['reproduction']['tests'][0]['stderr']
  response.update(root_cause='wrong return value', failure_digest='incorrect' if {bad_citation!r} else data['failure_digest'], reproduction_tests=['value regression'])
if '--output-format' in sys.argv:
 print(json.dumps({{'result':json.dumps(response),'session_id':'fake-only'}}))
else: print(json.dumps({{'result':json.dumps(response),'session_id':'fake-only'}}))
"""
        return self.fake_executable("workflow-provider", body)

    def step(self, snapshot, executable):
        return asyncio.run(run_step(self.ledger, TASK_ID, snapshot.revision, self.spec.config_digest, str(executable), "fake-only"))

    def install_role_skills(self) -> None:
        directory = self.project / "config" / "skills"
        directory.mkdir(parents=True, exist_ok=True)
        entries = []
        for name, role in (("builder-guidance", "builder"), ("debugger-guidance", "debugger")):
            source = f"# {name}\nController evidence remains authoritative.\n"
            path = directory / f"{name}.md"
            path.write_text(source, encoding="utf-8")
            entries.append(f'''[[skills]]
name = "{name}"
source_path = "config/skills/{name}.md"
source_url = "https://example.test/{name}.md"
source_commit = "{"a" * 40}"
git_blob_sha1 = "{"b" * 40}"
source_sha256 = "{hashlib.sha256(source.encode()).hexdigest()}"
license = "MIT"
supplied_version = "test-1"
scope = "workflow fixture guidance"
applicable_roles = ["{role}"]
dependencies = []
required_capabilities = []
''')
        (directory / "catalog.toml").write_text("schema_version = 1\n\n" + "\n".join(entries), encoding="utf-8")

    def test_role_specific_skills_are_bound_to_builder_and_debugger_packets(self) -> None:
        self.install_role_skills()
        self.spec = replace(self.spec, initial_manifest_json=workspace_manifest(self.project.resolve()))
        provider = self.provider(("def value(): return 41\n", "def value(): return 42\n"))
        failed = self.step(self.workflows.create(self.spec, "create"), provider)
        self.assertEqual(self.ledger.get(failed.active_task_id).spec.allowed_skills, ("builder-guidance",))
        fixed = self.step(failed, provider)
        self.assertEqual(self.ledger.get(fixed.active_task_id).spec.allowed_skills, ("debugger-guidance",))

    def test_builder_runs_required_tests_and_only_then_verifies_artifact(self) -> None:
        snapshot = self.workflows.create(self.spec, "create")
        provider = self.provider(("def value(): return 42\n", ""))
        result = self.step(snapshot, provider)
        self.assertEqual(result.status, WorkflowStatus.VERIFIED, result.detail)
        self.assertEqual(result.corrections_used, 0)
        self.assertEqual(self.workflows.replay(TASK_ID), result)
        records = self.ledger.evidence(result.active_task_id)
        tests = next(item for item in records if item.kind == "required_tests").to_dict()["payload"]
        self.assertTrue(tests["passed"])
        self.assertEqual(tests["tests"][0]["returncode"], 0)
        self.assertEqual(tests["candidate_digest"], digest_text(result.manifest_json))

    def test_debugger_reproduces_actual_failure_then_fixes_and_passes_same_test(self) -> None:
        provider = self.provider(("def value(): return 41\n", "def value(): return 42\n"))
        failed = self.step(self.workflows.create(self.spec, "create"), provider)
        self.assertEqual(failed.status, WorkflowStatus.NEEDS_CORRECTION)
        self.assertIsNotNone(failed.failure)
        original_test = json.loads(failed.failure.report_json)["tests"][0]
        self.assertFalse(original_test["passed"])
        fixed = self.step(failed, provider)
        self.assertEqual(fixed.status, WorkflowStatus.VERIFIED, fixed.detail)
        self.assertEqual(fixed.corrections_used, 1)
        self.assertNotEqual(failed.manifest_json, fixed.manifest_json)
        self.assertEqual(self.workflows.replay(TASK_ID), fixed)
        previous_evidence = self.ledger.evidence(failed.active_task_id)
        self.assertTrue(any(item.kind == "required_tests" and item.to_dict()["payload"]["phase"] == "reproduce:1"
                            and not item.to_dict()["payload"]["passed"] for item in previous_evidence))
        self.assertIn("debugger_diagnosis", {item.kind for item in self.ledger.evidence(fixed.active_task_id)})

    def test_correction_budget_is_durable_and_exhaustion_never_launches_again(self) -> None:
        spec = replace(self.spec, correction_round_limit=1)
        provider = self.provider(("def value(): return 0\n", "def value(): return 0\n"))
        failed = self.step(self.workflows.create(spec, "create"), provider)
        exhausted = self.step(failed, provider)
        self.assertEqual(exhausted.status, WorkflowStatus.EXHAUSTED)
        reopened = WorkflowStore(EventStore(self.ledger.database))
        self.assertEqual(reopened.replay(TASK_ID).corrections_used, 1)
        with self.assertRaises(ExecutionGuardError):
            self.step(exhausted, provider)
        self.assertEqual(len(self.ledger.list()), 2)

    def test_unowned_response_and_forged_debugger_citation_are_blocked(self) -> None:
        provider = self.provider(("def value(): return 0\n", "def value(): return 42\n"), bad_citation=True)
        failed = self.step(self.workflows.create(self.spec, "create"), provider)
        blocked = self.step(failed, provider)
        self.assertEqual(blocked.status, WorkflowStatus.BLOCKED)
        self.assertEqual((self.project / "src" / "value.py").read_text(), "def value(): return 0\n")
        with self.assertRaises(ValidationError):
            apply_response(self.spec, blocked.manifest_json, {"files": [{"relative_path": "tests/check.py", "base_sha256": None,
                                                                         "content": "pass"}], "summary": "forged"})

    def test_changed_human_file_prevents_dispatch_without_overwrite(self) -> None:
        snapshot = self.workflows.create(self.spec, "create")
        target = self.project / "tests" / "check.py"
        target.write_text("human edit\n")
        with self.assertRaises(ConflictError):
            self.step(snapshot, self.provider(("def value(): return 42", "")))
        self.assertEqual(target.read_text(), "human edit\n")
        self.assertEqual(self.ledger.list(), ())

    def test_start_is_one_shot_and_interrupted_workflow_is_quarantined(self) -> None:
        snapshot = self.workflows.create(self.spec, "create")
        active = self.workflows.start(TASK_ID, snapshot.revision, "start")
        with self.assertRaises(ConflictError):
            self.workflows.start(TASK_ID, snapshot.revision, "start")
        with self.assertRaises(ConflictError):
            self.step(active, self.provider(("def value(): return 42", "")))
        result = self.workflows.mutate(TASK_ID, "BLOCKED", {"reason": "explicit crash reconciliation"}, active.revision, "reconcile")
        self.assertEqual(result.status, WorkflowStatus.BLOCKED)
        self.assertEqual(self.ledger.list(), ())

    def test_event_failure_rolls_back_projection_and_command(self) -> None:
        snapshot = self.workflows.create(self.spec, "create")
        with closing(sqlite3.connect(self.ledger.database)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_workflow_event BEFORE INSERT ON workflow_events WHEN NEW.revision>1 BEGIN SELECT RAISE(ABORT,'fault'); END")
        with self.assertRaises(StorageError):
            self.workflows.start(TASK_ID, 1, "start")
        self.assertEqual(self.workflows.replay(TASK_ID), snapshot)
        self.assertEqual(self.workflows.create(self.spec, "create"), snapshot)
        with self.assertRaises(CommandConflictError):
            self.workflows.create(replace(self.spec, objective="changed"), "create")

    def test_stale_file_base_digest_and_symlink_escape_are_rejected(self) -> None:
        target = self.project / "src" / "value.py"
        target.write_text("human file")
        manifest = workspace_manifest(self.project.resolve())
        with self.assertRaises(ConflictError):
            apply_response(self.spec, manifest, {"files": [{"relative_path": "src/value.py", "base_sha256": "0" * 64,
                                                           "content": "replacement"}], "summary": "stale"})
        self.assertEqual(target.read_text(), "human file")
        target.unlink()
        target.symlink_to(self.base / "outside.py")
        with self.assertRaises(ValidationError):
            workspace_manifest(self.project.resolve())

    def test_trusted_verification_inputs_and_active_configuration_cannot_be_owned(self) -> None:
        config_path = self.project / "config" / "diamond.toml"
        config = load_config(config_path)
        for path in ("tests/check.py", "config/diamond.toml"):
            with self.assertRaises(ValidationError):
                protect_inputs(replace(self.spec, owned_paths=(path,)), config_path, config)
        protect_inputs(self.spec, config_path, config)

    def test_quarantined_workflow_retains_ownership_and_stale_writer_cannot_publish(self) -> None:
        active = self.workflows.start(TASK_ID, self.workflows.create(self.spec, "create").revision, "start")
        self.workflows.mutate(TASK_ID, "BLOCKED", {"reason": "explicit quarantine"}, active.revision, "block")
        with self.assertRaises(ConflictError):
            self.workflows.publish(active, {"files": [{"relative_path": "src/value.py", "base_sha256": None,
                                                      "content": "def value(): return 42"}], "summary": "late"})
        second = self.workflows.create(replace(self.spec, workflow_id="b" * 32), "other")
        with self.assertRaisesRegex(ConflictError, "overlaps"):
            self.workflows.start(second.spec.workflow_id, second.revision, "other-start")
        self.assertFalse((self.project / "src" / "value.py").exists())

    def test_unproven_test_cleanup_blocks_debugger_and_retains_test_result(self) -> None:
        provider = self.provider(("def value(): return 0\n", "def value(): return 42\n"))

        async def unproven(*args, **kwargs):
            return ExecutionResult(ExecutionStatus.CLEANUP_FAILED, None, "", "uncertain child", cleanup_complete=False)

        with patch("diamond.workflow.run", new=unproven):
            result = self.step(self.workflows.create(self.spec, "create"), provider)
        self.assertEqual(result.status, WorkflowStatus.BLOCKED)
        self.assertIn("test_result", {item.kind for item in self.ledger.evidence(result.active_task_id)})
        with self.assertRaises(ExecutionGuardError):
            self.step(result, provider)
        self.assertEqual(len(self.ledger.list()), 1)

    def test_valid_large_source_can_be_reproduced_and_corrected(self) -> None:
        bad = "# filler\n" * 2300 + "def value(): return 41\n"
        good = "# filler\n" * 2300 + "def value(): return 42\n"
        provider = self.provider((bad, good))
        failed = self.step(self.workflows.create(self.spec, "create"), provider)
        self.assertEqual(failed.status, WorkflowStatus.NEEDS_CORRECTION, failed.detail)
        fixed = self.step(failed, provider)
        self.assertEqual(fixed.status, WorkflowStatus.VERIFIED, fixed.detail)
        self.assertEqual(fixed.corrections_used, 1)

    def test_interrupted_second_test_retains_first_result_and_second_launch_identity(self) -> None:
        spec = replace(self.spec, tests=(self.spec.tests[0], replace(self.spec.tests[0], name="second regression")))
        provider = self.provider(("def value(): return 42\n", ""))
        calls = []

        async def interrupted(*args, **kwargs):
            kwargs["on_started"](123, None)
            calls.append(1)
            if len(calls) == 2:
                raise asyncio.CancelledError
            return ExecutionResult(ExecutionStatus.SUCCEEDED, 0, "first passed", "")

        with patch("diamond.workflow.run", new=interrupted):
            with self.assertRaises(asyncio.CancelledError):
                self.step(self.workflows.create(spec, "create"), provider)
        snapshot = WorkflowStore(EventStore(self.ledger.database)).replay(TASK_ID)
        self.assertEqual(snapshot.status, WorkflowStatus.BLOCKED)
        records = self.ledger.evidence(snapshot.active_task_id)
        self.assertEqual(sum(item.kind == "test_result" for item in records), 1)
        self.assertEqual(sum(item.kind == "test_launch_intent" for item in records), 2)
        self.assertEqual(sum(item.kind == "test_process_started" for item in records), 2)

    def test_module_verification_harness_cannot_be_owned(self) -> None:
        (self.project / "checks").mkdir()
        (self.project / "checks" / "regression.py").write_text("assert True\n")
        test = TestCommand("module regression", (sys.executable, "-B", "-m", "checks.regression"))
        config_path = self.project / "config" / "diamond.toml"
        config = load_config(config_path)
        with self.assertRaises(ValidationError):
            protect_inputs(replace(self.spec, tests=(test,), owned_paths=("checks/regression.py",)), config_path, config)
        protect_inputs(replace(self.spec, tests=(test,)), config_path, config)
        (self.project / "verify.py").write_text("assert True\n")
        with self.assertRaises(ValidationError):
            protect_inputs(replace(self.spec, tests=(TestCommand("standalone", (sys.executable, "-B", "-m", "verify")),),
                                   owned_paths=("verify.py",)), config_path, config)

    def test_large_uncorrectable_source_is_rejected_before_publication(self) -> None:
        provider = self.provider(("# filler\n" * 8000 + "def value(): return 0\n", ""))
        result = self.step(self.workflows.create(self.spec, "create"), provider)
        self.assertEqual(result.status, WorkflowStatus.BLOCKED)
        self.assertIn("correction-context budget", result.detail)
        self.assertFalse((self.project / "src" / "value.py").exists())
        self.assertEqual(result.corrections_used, 0)

    def test_json_escape_heavy_source_cannot_exceed_correction_prompt_budget(self) -> None:
        content = "#" + "\\" * 32700 + "\ndef value(): return 41\n"
        with self.assertRaisesRegex(ValidationError, "serialized"):
            apply_response(self.spec, self.spec.initial_manifest_json, {"files": [{"relative_path": "src/value.py",
                "base_sha256": None, "content": content}], "summary": "escape-heavy source"})
        self.assertFalse((self.project / "src" / "value.py").exists())

    def test_debugger_context_keeps_failure_message_after_long_traceback(self) -> None:
        harness = self.project / "tests" / "check.py"
        harness.write_text("import sys\nsys.stderr.write('x' * 8300 + '\\n')\n" + harness.read_text())
        self.spec = replace(self.spec, initial_manifest_json=workspace_manifest(self.project.resolve()))
        provider = self.provider(("def value(): return 41\n", "def value(): return 42\n"), require_failure_tail=True)
        failed = self.step(self.workflows.create(self.spec, "create"), provider)
        outcome = json.loads(failed.failure.report_json)["tests"][0]
        self.assertEqual(len(outcome["stderr"]), 8192)
        self.assertIn("AssertionError", outcome["stderr"])
        self.assertNotEqual(outcome["stderr_sha256"], digest_text(outcome["stderr"]))
        fixed = self.step(failed, provider)
        self.assertEqual(fixed.status, WorkflowStatus.VERIFIED, fixed.detail)
