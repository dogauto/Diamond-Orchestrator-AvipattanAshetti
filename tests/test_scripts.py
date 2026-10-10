import os
import shutil
import subprocess
import sys

from support import ROOT, ProjectCase


class ScriptTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        ignore_generated = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(ROOT / "src", self.project / "src", ignore=ignore_generated)
        shutil.copytree(ROOT / "scripts", self.project / "scripts", ignore=ignore_generated)

    def launch(self, script: str, *arguments: str, python: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["/bin/sh", str(self.project / "scripts" / script), *arguments], cwd=self.base,
                              env=dict(os.environ, DIAMOND_PYTHON=python or sys.executable),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)

    def test_launcher_foreign_cwd_spaced_project_and_python_paths(self) -> None:
        interpreter = self.base / "python with spaces"
        interpreter.symlink_to(sys.executable)
        result = self.launch("diamond.sh", "--json", "task", "list", python=str(interpreter))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"tasks": []', result.stdout)
        result = self.launch("diamond.sh", "validate-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.project / "config" / "diamond.toml"), result.stdout)
        self.assertFalse((self.base / ".diamond").exists())
        self.assertFalse((self.project / ".diamond").exists())

    def test_launcher_forwards_exit_codes_and_rejects_old_or_missing_python(self) -> None:
        result = self.launch("diamond.sh", "task", "show", "a" * 32)
        self.assertEqual(result.returncode, 3)
        old = self.fake_executable("old python", "raise SystemExit(2)")
        result = self.launch("diamond.sh", "--help", python=str(old))
        self.assertEqual(result.returncode, 2)
        self.assertIn("Python >=3.11", result.stderr)
        result = self.launch("diamond.sh", "--help", python=str(self.base / "missing python"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("not found", result.stderr)
        result = self.launch("diamond.sh", "--version")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "diamond 0.1.0")

    def test_check_script_refuses_zero_tests(self) -> None:
        (self.project / "tests").mkdir()
        result = self.launch("check.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no tests discovered", result.stderr)

    def test_check_script_runs_tests_and_compiles_without_bytecode(self) -> None:
        tests = self.project / "tests"
        tests.mkdir()
        (tests / "test_meaningful.py").write_text(
            "import unittest\nfrom diamond.config import ControllerConfig\nfrom diamond.errors import ValidationError\n"
            "class Checks(unittest.TestCase):\n"
            "    def test_bool_rejected(self):\n"
            "        with self.assertRaises(ValidationError): ControllerConfig(True, 900, 2)\n", encoding="utf-8")
        result = self.launch("check.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: 1 tests", result.stdout)
        self.assertFalse(list(self.project.rglob("*.pyc")))
        (self.project / "src" / "diamond" / "broken.py").write_text("def broken(\n", encoding="utf-8")
        result = self.launch("check.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PASS:", result.stdout)
