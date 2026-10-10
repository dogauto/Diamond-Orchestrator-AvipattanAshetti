import io
import json
import os
import shutil
import sqlite3
import sys
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from diamond.cli import main
from diamond.diagnostics import ProbeResult
from diamond.domain import Provider
from diamond.errors import ValidationError
from diamond.self_improve import validate_owned_paths
from support import CONFIG_TEXT, ROOT, TASK_ID, ProjectCase


class CliTests(ProjectCase):
    def test_deep_toml_cli_is_invalid_config_not_internal_error(self) -> None:
        source = "x=" + "{x=" * 1000 + "1" + "}" * 1000
        self.assertLess(len(source.encode("utf-8")), 4096)
        (self.project / "config" / "diamond.toml").write_text(source, encoding="utf-8")
        environment = self.sentinel_environment()
        for arguments in (("validate-config",), ("task", "list"), ("task", "create", "--objective", "Validate before writing")):
            result = self.cli_process("--json", *arguments, environment=environment)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_CONFIG")
            self.assertEqual(result.stderr, "")
        self.assertFalse((self.project / ".diamond").exists())
        self.assertFalse(self.sentinel.exists())

    def sentinel_environment(self) -> dict[str, str]:
        binary_dir = self.base / "sentinel-bin"
        binary_dir.mkdir()
        self.sentinel = self.base / "provider-was-called"
        for provider in Provider:
            executable = self.fake_executable(provider.value, f"from pathlib import Path\nPath({str(self.sentinel)!r}).write_text('forbidden')\nraise SystemExit(88)")
            executable.rename(binary_dir / provider.value)
        return {"PATH": str(binary_dir) + os.pathsep + "/usr/bin:/bin"}

    def test_black_box_create_restart_list_show_events_no_provider_calls(self) -> None:
        env = self.sentinel_environment()
        created = self.cli_process("--json", "task", "create", "--objective", "Ledger only", "--task-id", TASK_ID,
                                   "--command-id", "create:1", environment=env)
        self.assertEqual(created.returncode, 0, created.stderr)
        data = json.loads(created.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["task"]["status"], "CREATED")
        self.assertEqual(data["result"]["task"]["spec"]["provider"], "claude")
        for command, field in ((["list"], "tasks"), (["show", TASK_ID], "task"), (["events", TASK_ID], "events")):
            result = self.cli_process("--json", "task", *command, environment=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = json.loads(result.stdout)["result"][field]
            if field == "task":
                self.assertEqual(content, data["result"]["task"])
            elif field == "tasks":
                self.assertEqual(content, [data["result"]["task"]])
            else:
                self.assertEqual([(event["revision"], event["event_type"]) for event in content], [(1, "TASK_CREATED")])
        valid = self.cli_process("validate-config", "--json", environment=env)
        self.assertEqual(valid.returncode, 0)
        self.assertFalse(self.sentinel.exists())

    def test_missing_reads_and_bad_requests_never_initialize_state(self) -> None:
        env = self.sentinel_environment()
        empty = self.cli_process("task", "list", "--json", environment=env)
        self.assertEqual(empty.returncode, 0)
        self.assertEqual(json.loads(empty.stdout)["result"]["tasks"], [])
        for command in ("show", "events"):
            missing = self.cli_process("--json", "task", command, TASK_ID, environment=env)
            self.assertEqual(missing.returncode, 3)
            self.assertEqual(json.loads(missing.stdout)["error"]["code"], "NOT_FOUND")
        invalid_arguments = [
            ["task", "create", "--objective", " "],
            ["task", "create", "--objective", "test", "--task-id", "../escape"],
            ["task", "create", "--objective", "test", "--command-id", "../escape"],
            ["task", "create", "--objective", "test", "--provider", "unknown"],
            ["task", "show", "BAD"], ["task", "create"], ["not-a-command"],
        ]
        for arguments in invalid_arguments:
            result = self.cli_process("--json", *arguments, environment=env)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertFalse(json.loads(result.stdout)["ok"])
            self.assertEqual(result.stderr, "")
        self.assertFalse((self.project / ".diamond").exists())
        self.assertFalse(self.sentinel.exists())

    def test_cli_command_id_retry_and_changed_input_conflict(self) -> None:
        arguments = ("--json", "task", "create", "--objective", "Idempotent", "--command-id", "retry:1")
        first = self.cli_process(*arguments)
        second = self.cli_process(*arguments)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        changed = self.cli_process("--json", "task", "create", "--objective", "Changed", "--command-id", "retry:1")
        self.assertEqual(changed.returncode, 4)
        self.assertEqual(json.loads(changed.stdout)["error"]["code"], "COMMAND_CONFLICT")

    def test_config_override_disabled_explicit_provider_and_config_failure(self) -> None:
        config = self.project / "alternate.toml"
        source = CONFIG_TEXT.replace('enabled = true', 'enabled = false', 1)
        source = source.replace('planner = "gemini"', 'planner = "claude"').replace('researcher = "gemini"', 'researcher = "claude"')
        config.write_text(source, encoding="utf-8")
        result = self.cli_process("--config", "alternate.toml", "--json", "task", "create", "--objective", "test", "--provider", "gemini")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.project / ".diamond").exists())
        config.write_text("malformed = [", encoding="utf-8")
        result = self.cli_process("--config", "alternate.toml", "--json", "validate-config")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "INVALID_CONFIG")

    def test_doctor_json_envelope_mocked_success_failure_and_no_state(self) -> None:
        for missing in (False, True):
            results = tuple(ProbeResult(provider, "missing" if missing and provider is Provider.GEMINI else "available",
                                        "/fake/tool", None if missing and provider is Provider.GEMINI else "1.2.3") for provider in Provider)
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("diamond.cli.doctor", return_value=results), redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--project", str(self.project), "doctor", "--json"])
            self.assertEqual(code, 1 if missing else 0)
            data = json.loads(stdout.getvalue())
            self.assertEqual(len(data["result"]["providers"]), 6)
            self.assertEqual(data["result"]["auth_status"], "NOT_CHECKED")
            self.assertEqual(data["result"]["workflow_execution"], "UNAVAILABLE")
            self.assertEqual(stderr.getvalue(), "")
        self.assertFalse((self.project / ".diamond").exists())

    def test_black_box_doctor_uses_only_fake_version_executables(self) -> None:
        binary_dir = self.base / "fake-versions"
        binary_dir.mkdir()
        names = {Provider.ANTIGRAVITY: "agy"}
        for provider in Provider:
            name = names.get(provider, provider.value)
            executable = self.fake_executable(name, "import sys\nassert sys.argv[1:] == ['--version']\nprint('fake 1.2.3')")
            executable.rename(binary_dir / name)
        result = self.cli_process("doctor", "--json", environment={"PATH": str(binary_dir)})
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual([probe["version"] for probe in data["result"]["providers"]], ["1.2.3"] * 6)
        self.assertFalse((self.project / ".diamond").exists())

    def test_project_status_json_is_complete_and_provider_probe_is_bounded(self) -> None:
        binary_dir = self.base / "fake-status-versions"
        binary_dir.mkdir()
        names = {Provider.ANTIGRAVITY: "agy"}
        for provider in Provider:
            name = names.get(provider, provider.value)
            executable = self.fake_executable(name, "import sys\nassert sys.argv[1:] == ['--version']\nprint('fake 1.2.3')")
            executable.rename(binary_dir / name)

        result = self.cli_process("project", "status", "--json", environment={"PATH": str(binary_dir)})

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(set(data["result"]), {"config", "deliveries", "project", "providers", "skills", "workflows"})
        self.assertEqual(len(data["result"]["providers"]), 6)
        self.assertEqual(data["result"]["skills"], [])
        self.assertEqual(data["result"]["workflows"], [])
        self.assertEqual(data["result"]["deliveries"], [])
        self.assertEqual({probe["status"] for probe in data["result"]["providers"]}, {"available"})
        self.assertFalse((self.project / ".diamond").exists())

    def test_workflow_status_alias_matches_show_and_list_is_machine_readable(self) -> None:
        workspace = self.base / "workflow-workspace"
        workspace.mkdir()
        (workspace / "app.py").write_text("print('ready')\n", encoding="utf-8")
        (workspace / "output.py").write_text("value = 1\n", encoding="utf-8")
        test = json.dumps({"name": "smoke", "argv": [sys.executable, "app.py"], "timeout_seconds": 5, "output_limit": 4096})
        created = self.cli_process(
            "--json", "workflow", "create", "--objective", "Inspect workflow status",
            "--workspace", str(workspace), "--owned-path", "output.py", "--test", test,
            "--workflow-id", TASK_ID, "--command-id", "workflow:create:1",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        workflow = json.loads(created.stdout)["result"]["workflow"]

        listed = self.cli_process("--json", "workflow", "list")
        shown = self.cli_process("--json", "workflow", "show", TASK_ID)
        aliased = self.cli_process("--json", "workflow", "status", TASK_ID)

        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(aliased.returncode, 0, aliased.stderr)
        self.assertEqual(json.loads(listed.stdout)["result"]["workflows"], [workflow])
        expected = json.loads(shown.stdout)["result"]["workflow"]
        self.assertEqual(expected, workflow)
        self.assertEqual(json.loads(aliased.stdout)["result"]["workflow"], expected)

    def test_black_box_doctor_oversized_version_returns_all_five_results(self) -> None:
        binary_dir = self.base / "fake-versions"
        binary_dir.mkdir()
        names = {Provider.ANTIGRAVITY: "agy"}
        for provider in Provider:
            output = "print('1.2.' + '3' * 5000)" if provider is Provider.GEMINI else "print('fake 1.2.3')"
            name = names.get(provider, provider.value)
            executable = self.fake_executable(name, "import sys\nassert sys.argv[1:] == ['--version']\n" + output)
            executable.rename(binary_dir / name)
        result = self.cli_process("doctor", "--json", environment={"PATH": str(binary_dir)})
        self.assertEqual(result.returncode, 1, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["ok"])
        self.assertNotIn("error", data)
        self.assertEqual([probe["status"] for probe in data["result"]["providers"]], ["invalid_output"] + ["available"] * 5)
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.project / ".diamond").exists())

    def test_storage_errors_and_explicit_external_state_directory(self) -> None:
        state = self.base / "external state"
        result = self.cli_process("--state-dir", str(state), "--json", "task", "create", "--objective", "External isolated state", "--task-id", TASK_ID)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((state / "diamond.sqlite3").is_file())
        self.assertFalse((self.project / ".diamond").exists())
        with closing(sqlite3.connect(state / "diamond.sqlite3")) as connection, connection:
            connection.execute("PRAGMA user_version=999")
        result = self.cli_process("--state-dir", str(state), "--json", "task", "show", TASK_ID)
        self.assertEqual(result.returncode, 5)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "STORAGE_ERROR")

    def test_human_errors_are_actionable_without_traceback(self) -> None:
        result = self.cli_process("task", "show", TASK_ID)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout, "")
        self.assertIn("NOT_FOUND", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_task_commands_do_not_call_provider_even_in_process(self) -> None:
        with patch("diamond.cli.doctor", side_effect=AssertionError("provider calls forbidden")), \
             patch("subprocess.Popen", side_effect=AssertionError("external invocation forbidden")), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--project", str(self.project), "validate-config"]), 0)
            self.assertEqual(main(["--project", str(self.project), "task", "create", "--objective", "No invocation", "--task-id", TASK_ID]), 0)
            self.assertEqual(main(["--project", str(self.project), "task", "list"]), 0)
            self.assertEqual(main(["--project", str(self.project), "task", "show", TASK_ID]), 0)
            self.assertEqual(main(["--project", str(self.project), "task", "events", TASK_ID]), 0)

    def test_self_improvement_checkpoint_uses_workflow_id_across_lifecycle(self) -> None:
        shutil.copytree(ROOT / "src", self.project / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "tests", self.project / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "scripts", self.project / "scripts")
        state = self.base / "self-improve-state"
        test = json.dumps({
            "name": "controller suite",
            "argv": [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            "timeout_seconds": 120,
            "output_limit": 65536,
        })
        created = self.cli_process(
            "--state-dir", str(state), "--json", "self-improve", "create",
            "--objective", "Review workflow status", "--owned-path", "src/diamond/workflow_cli.py",
            "--test", test, "--command-id", "self-improve:create:1",
        )
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        created_data = json.loads(created.stdout)["result"]
        workflow_id = created_data["workflow"]["spec"]["workflow_id"]
        checkpoint = Path(created_data["checkpoint"])
        self.assertTrue(checkpoint.is_dir())
        self.assertEqual(checkpoint.name, workflow_id)

        status = self.cli_process("--state-dir", str(state), "--json", "self-improve", "status", workflow_id)
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        status_data = json.loads(status.stdout)["result"]
        self.assertEqual(Path(status_data["checkpoint"]), checkpoint)
        self.assertTrue(Path(status_data["checkpoint"]).is_dir())

        rollback = self.cli_process("--state-dir", str(state), "--json", "self-improve", "rollback", workflow_id)
        self.assertEqual(rollback.returncode, 0, rollback.stdout + rollback.stderr)
        self.assertEqual(json.loads(rollback.stdout)["result"]["rolled_back"], ["src/diamond/workflow_cli.py"])

    def test_self_improvement_cannot_own_controller_policy_files(self) -> None:
        shutil.copytree(ROOT / "src", self.project / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        protected = (
            "src/diamond/self_improve.py", "src/diamond/workspace.py", "src/diamond/sandbox.py",
            "src/diamond/packets.py", "src/diamond/workflow_store.py", "src/diamond/delivery_store.py",
            "src/diamond/controller.py", "src/diamond/skills.py",
        )
        for relative in protected:
            with self.subTest(relative=relative), self.assertRaises(ValidationError):
                validate_owned_paths(self.project, (relative,))
