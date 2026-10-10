import asyncio
import json
import shutil
import sys
import unittest
from dataclasses import replace
from uuid import uuid4

import test_delivery as fixtures
from diamond.errors import ExecutionGuardError, ValidationError
from diamond.test_inputs import capture_test_inputs
from diamond.workflow_contracts import TestCommand
from diamond.domain import Provider
from diamond.workflow import run_step
from diamond.workflow_contracts import WorkflowSpec, WorkflowStatus
from diamond.workflow_store import WorkflowStore
from diamond.workspace import workspace_manifest


TEST_SOURCE = ("import sys,unittest\nsys.path.insert(0,'.')\nfrom src.value import value\n"
               "class ValueTest(unittest.TestCase):\n def test_value(self): self.assertEqual(value(),42)\n")


class TestInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.DeliveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_external_discovery_membership_changes_invalidate_certificate(self) -> None:
        t = self.fixture
        directory = t.base.resolve() / "external-tests"
        directory.mkdir()
        (directory / "test_value.py").write_text(TEST_SOURCE)
        runner = t.base.resolve() / "verify.py"
        runner.write_text("import sys,unittest\nsuite=unittest.defaultTestLoader.discover(sys.argv[1])\n"
                          "sys.exit(0 if unittest.TextTestRunner().run(suite).wasSuccessful() else 1)\n")
        t.tests = (TestCommand("value acceptance", (sys.executable, "-B", str(runner), str(directory))),)
        accepted = t.pipeline(t.create(t.component()))[-1]
        inputs = json.loads(accepted.test_inputs_json)
        self.assertIn(str(directory), inputs["directories"])
        (directory / "test_added.py").write_text("import unittest\nclass Added(unittest.TestCase):\n def test_fail(self): self.fail('newly discovered failure')\n")
        with self.assertRaises(ExecutionGuardError):
            t.deliveries.certificate(accepted.spec.delivery_id, t.config_digest)
        self.assertEqual(t.deliveries.get(accepted.spec.delivery_id).status.value, "STALE")

    def test_installed_stdlib_module_runner_is_bound_and_runs_actual_assertions(self) -> None:
        t = self.fixture
        (t.root / "tests/acceptance.py").write_text(TEST_SOURCE)
        source = t.component()
        t.tests = (TestCommand("value acceptance", (sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "acceptance.py")),)
        accepted = t.pipeline(t.create(source))[-1]
        inputs = json.loads(accepted.test_inputs_json)
        self.assertTrue(any("unittest" in path for path in inputs["directories"]))
        self.assertTrue(t.deliveries.certificate(accepted.spec.delivery_id, t.config_digest))
        for task in t.ledger.list():
            for item in t.ledger.evidence(task.spec.task_id):
                payload = item.to_dict()["payload"]
                if item.kind == "required_tests" and payload["phase"].startswith("delivery:"):
                    self.assertIn("Ran 1 test", payload["tests"][0]["stderr"])
                    self.assertEqual(payload["tests"][0]["returncode"], 0)

    def test_source_cannot_shadow_its_trusted_stdlib_runner(self) -> None:
        t = self.fixture
        created = t.create(t.component())
        spec = replace(created.spec.work_spec(), tests=(TestCommand("module", (sys.executable, "-B", "-m", "unittest")),),
                       owned_paths=("unittest/__main__.py",))
        with self.assertRaisesRegex(ValidationError, "shadow"):
            capture_test_inputs(spec)

    def test_inherited_pythonpath_cannot_redirect_pinned_stdlib_checks_to_owned_source(self) -> None:
        t = self.fixture
        (t.root / "tests/acceptance.py").write_text(TEST_SOURCE)
        t.tests = (TestCommand("value acceptance", (sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "acceptance.py")),)
        identifier = uuid4().hex
        root = t.base.resolve() / f"source-{identifier}"
        shutil.copytree(t.root, root)
        contents = {"src/value.py": "def value(): return 41\n", "src/unittest.py": "print('shadow runner: no assertions')\n"}
        fake = t.fake_executable(f"provider-{identifier}", f"""import json,sys
data=json.loads(sys.argv[-1].split('\\n',1)[1]); contents={contents!r}
response={{'files':[{{'relative_path':f['relative_path'],'base_sha256':f['base_sha256'],'content':contents[f['relative_path']]}} for f in data['owned_files']], 'summary':'verified component'}}
print(json.dumps({{'result':json.dumps(response),'session_id':'author-{identifier}'}}))
""")
        check = TestCommand("source test", (sys.executable, "-B", "-c", "from src.value import value; assert value()==41"))
        spec = WorkflowSpec(identifier, "Create an explicit weak-upstream fixture", str(root), tuple(contents), (check,),
            Provider.CLAUDE, Provider.CODEX, t.config_digest, workspace_manifest(root), timeout_seconds=10)
        created = WorkflowStore(t.ledger).create(spec, f"source:{identifier}")
        source = asyncio.run(run_step(t.ledger, identifier, created.revision, t.config_digest, str(fake), "fake-only"))
        self.assertEqual(source.status, WorkflowStatus.VERIFIED, source.detail)
        rejected = t.step(t.create(source))
        self.assertEqual(rejected.status.value, "REJECTED", rejected.detail)
        with self.assertRaises(ExecutionGuardError):
            t.deliveries.certificate(rejected.spec.delivery_id, t.config_digest)
        report = next(item.to_dict()["payload"] for item in t.ledger.evidence(rejected.active_task_id)
                      if item.kind == "required_tests")
        self.assertEqual(report["tests"][0]["returncode"], 1)
        self.assertIn("41 != 42", report["tests"][0]["stderr"])
        self.assertNotIn("shadow runner", report["tests"][0]["stdout"])
