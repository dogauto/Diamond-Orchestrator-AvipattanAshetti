import asyncio
import hashlib
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from diamond.cli import main
from diamond.diagnostics import ProbeResult
from diamond.domain import Provider
from diamond.errors import ExecutionGuardError
from diamond.self_improve import publish_verified_changes
from diamond.store import EventStore
from diamond.delivery import run_stage
import test_delivery
from support import ProjectCase


class SelfImproveTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project = self.project.resolve()
        self.state = self.base.resolve() / "improvement-state"
        self.source = self.project / "src/diamond/feature.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("value = 1\n")
        self.other = self.source.parent / "other.py"
        self.other.write_text("value = 1\n")
        (self.source.parent / "__init__.py").write_text("")
        (self.project / "tests").mkdir()
        (self.project / "tests/check.py").write_text("import sys\nsys.path.insert(0, 'src')\nfrom diamond.feature import value\nassert value == 2\n")
        self.test = {"name": "value", "argv": [sys.executable, "-B", "tests/check.py"],
                     "timeout_seconds": 10, "output_limit": 4096}
        self.builder = self.fake_executable("builder", """import sys,json
data=json.loads(sys.argv[-1].split('\\n',1)[1])
files=[{'relative_path':f['relative_path'],'base_sha256':f['base_sha256'],'content':'value = 2\\n'} for f in data['owned_files']]
print(json.dumps({'result':json.dumps({'files':files,'summary':'value fix'}),'session_id':'isolated-builder'}))
""")
        result = self.command("create", "--objective", "Correct value", "--owned-path", "src/diamond/feature.py",
                              "--owned-path", "src/diamond/other.py",
                              "--test", json.dumps(self.test), "--command-id", "improve-fixture")
        self.workflow_id = result["workflow"]["spec"]["workflow_id"]
        self.checkpoint = Path(result["checkpoint"])
        self.ledger = EventStore(self.state / "diamond.sqlite3")
        self.config_digest = hashlib.sha256((self.project / "config/diamond.toml").read_bytes()).hexdigest()

    def command(self, *args, success=True):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--project", str(self.project), "--state-dir", str(self.state),
                         "--json", "self-improve", *args])
        data = json.loads(output.getvalue())
        if success:
            self.assertEqual(code, 0, data)
            return data["result"]
        self.assertNotEqual(code, 0, data)
        return data

    def verified(self):
        probe = ProbeResult(Provider.CLAUDE, "available", str(self.builder), "fake-only", 0)
        with patch("diamond.workflow_cli.probe_version", return_value=probe):
            return self.command("run", self.workflow_id, "--expected-revision", "1")

    def test_verified_run_keeps_live_source_unchanged_until_independent_review(self):
        result = self.verified()
        self.assertEqual(result["workflow"]["status"], "VERIFIED")
        self.assertEqual(self.source.read_text(), "value = 1\n")
        self.assertNotIn("applied_to_project", result)

    def test_unreviewed_low_level_publication_is_rejected(self):
        with self.assertRaises(ExecutionGuardError):
            publish_verified_changes(self.project, self.checkpoint, ("src/diamond/feature.py",))

    def test_rollback_without_publication_cannot_erase_a_human_edit(self):
        self.source.write_text("human_edit = True\n")
        self.command("rollback", self.workflow_id, success=False)
        self.assertEqual(self.source.read_text(), "human_edit = True\n")

    def accepted(self):
        self.verified()
        result = self.command("review", self.workflow_id, "--command-id", "independent-review")
        current = result["delivery"]
        provider = test_delivery.DeliveryTests.provider(self)
        for _ in range(4):
            current = asyncio.run(run_stage(self.ledger, current["spec"]["delivery_id"], current["revision"],
                                            self.config_digest, str(provider), "fake-only")).to_dict()
        self.assertEqual(current["status"], "ACCEPTED", current)
        return current["spec"]["delivery_id"]

    def test_reviewed_publication_and_rollback_are_idempotent_and_durable(self):
        identifier = self.accepted()
        result = self.command("publish", self.workflow_id, "--delivery-id", identifier)
        self.assertEqual(result["publication"]["status"], "PUBLISHED")
        self.assertEqual(self.source.read_text(), "value = 2\n")
        self.assertEqual(self.command("publish", self.workflow_id, "--delivery-id", identifier), result)
        rolled_back = self.cli_process("--state-dir", str(self.state), "--json", "self-improve", "rollback", self.workflow_id)
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stdout)
        self.assertEqual(self.source.read_text(), "value = 1\n")
        self.assertEqual(self.other.read_text(), "value = 1\n")
        self.assertEqual(self.command("rollback", self.workflow_id)["rolled_back"], [])
        self.command("publish", self.workflow_id, "--delivery-id", identifier, success=False)

    def test_published_rollback_preserves_newer_bytes_and_modes(self):
        identifier = self.accepted()
        self.command("publish", self.workflow_id, "--delivery-id", identifier)
        self.source.write_text("human = 3\n")
        self.command("rollback", self.workflow_id, success=False)
        self.assertEqual(self.source.read_text(), "human = 3\n")
        self.assertEqual(self.other.read_text(), "value = 2\n")
        self.assertEqual(self.command("status", self.workflow_id)["publication"]["status"], "BLOCKED")

    def test_review_target_and_component_tampering_block_publication(self):
        identifier = self.accepted()
        for target in (self.checkpoint / "workspace/src/diamond/feature.py",
                       self.checkpoint / "review/src/diamond/feature.py"):
            original = target.read_bytes()
            target.write_text("unreviewed = True\n")
            self.command("publish", self.workflow_id, "--delivery-id", identifier, success=False)
            self.assertEqual(self.source.read_text(), "value = 1\n")
            target.write_bytes(original)

    def test_dependency_change_invalidates_publication(self):
        identifier = self.accepted()
        (self.source.parent / "__init__.py").write_text("new_dependency = True\n")
        self.command("publish", self.workflow_id, "--delivery-id", identifier, success=False)
        self.assertEqual(self.source.read_text(), "value = 1\n")

    def test_cancel_fences_a_verified_candidate_across_restart(self):
        identifier = self.accepted()
        self.command("cancel", self.workflow_id, "--reason", "stop trial")
        result = self.cli_process("--state-dir", str(self.state), "--json", "self-improve", "publish",
                                  self.workflow_id, "--delivery-id", identifier)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.source.read_text(), "value = 1\n")

    def test_interrupted_multifile_publication_recovers_in_fresh_process(self):
        from diamond.improvement_publication import _replace
        identifier = self.accepted()

        def interrupted(*args):
            _replace(*args)
            raise KeyboardInterrupt()

        with patch("diamond.improvement_publication._replace", side_effect=interrupted):
            self.command("publish", self.workflow_id, "--delivery-id", identifier, success=False)
        self.assertEqual(self.source.read_text(), "value = 2\n")
        self.assertEqual(self.other.read_text(), "value = 1\n")
        self.assertEqual(self.command("status", self.workflow_id)["publication"]["status"], "PUBLISHING")
        result = self.cli_process("--state-dir", str(self.state), "--json", "self-improve", "reconcile", self.workflow_id)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.source.read_text(), "value = 1\n")
        self.assertEqual(self.other.read_text(), "value = 1\n")
