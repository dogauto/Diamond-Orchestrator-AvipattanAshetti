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
from support import ProjectCase


class SelfImproveTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project = self.project.resolve()
        self.state = self.base.resolve() / "improvement-state"
        self.source = self.project / "src/diamond/feature.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("value = 1\n")
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
                              "--test", json.dumps(self.test), "--command-id", "improve-fixture")
        self.workflow_id = result["workflow"]["spec"]["workflow_id"]
        self.checkpoint = Path(result["checkpoint"])

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
