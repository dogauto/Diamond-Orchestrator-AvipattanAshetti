import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from diamond.shell import DiamondShell
from diamond.shell_commands import _dispatch, cmd_chat
from diamond.shell_ui import _format_status
from support import ROOT, TASK_ID, ProjectCase


class ShellTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.shell = DiamondShell(str(self.project))

    def dispatch(self, command: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            _dispatch(self.shell, command)
        return output.getvalue()

    def test_task_creation_displays_identifier_and_show_displays_objective(self) -> None:
        output = self.dispatch(f'task create --objective "Shell lifecycle" --task-id {TASK_ID}')
        self.assertIn(TASK_ID, output)
        self.assertIn("Shell lifecycle", self.dispatch(f"task show {TASK_ID}"))
        self.assertIn("TASK_CREATED", self.dispatch(f"task events {TASK_ID}"))

    def test_validation_and_missing_record_errors_are_visible(self) -> None:
        self.assertIn("valid", self.dispatch("validate"))
        result, ok = self.shell._run_cmd(["task", "show", TASK_ID])
        self.assertFalse(ok)
        self.assertEqual(result["error"]["code"], "NOT_FOUND")

    def test_provider_configuration_dispatches_to_controller_without_network(self) -> None:
        with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
            self.dispatch("provider configure freellmapi --url http://127.0.0.1:31415")
        self.assertTrue((self.project / ".diamond/freellmapi.json").is_file())

    def test_malformed_quote_does_not_terminate_interactive_session(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", "-m", "diamond", "--project", str(self.project), "shell"],
            input='task create --objective "broken\nvalidate\nquit\n', text=True,
            capture_output=True, timeout=10, cwd=self.base,
            env=dict(os.environ, HOME=str(self.base), PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No closing quotation", result.stdout)
        self.assertIn("valid", result.stdout)
        self.assertFalse((self.project / ".diamond").exists())

    def test_clear_returns_success_without_spurious_unpacking_error(self) -> None:
        with patch("diamond.shell_commands.os.system", return_value=0):
            self.assertNotIn("ERROR", self.dispatch("clear"))

    def test_workflow_step_preserves_arguments_instead_of_inventing_revision(self) -> None:
        shell = Mock()
        shell._run_cmd.return_value = ({}, True)
        _dispatch(shell, f"workflow step {TASK_ID} --bogus")
        shell._run_cmd.assert_called_once_with(["--json", "workflow", "step", TASK_ID, "--bogus"])

    def test_creation_commands_forward_supplied_arguments(self) -> None:
        shell = Mock()
        shell._run_cmd.return_value = ({}, True)
        for command in ("workflow create", "self-improve create"):
            with self.subTest(command=command):
                shell._run_cmd.reset_mock()
                _dispatch(shell, command + ' --objective "bounded change"')
                shell._run_cmd.assert_called_once_with(["--json", *command.split(), "--objective", "bounded change"])

    def test_status_uses_nested_workflow_and_delivery_identifiers(self) -> None:
        output = _format_status({"workflows": [{"spec": {"workflow_id": TASK_ID}, "status": "CREATED"}],
                                 "deliveries": [{"spec": {"delivery_id": "b" * 32}, "status": "CREATED"}]}, True)
        self.assertIn(TASK_ID[:12], output)
        self.assertIn("b" * 12, output)

    def test_chat_status_uses_project_status_without_model_request(self) -> None:
        shell = Mock(project_root=str(self.project))
        shell._run_cmd.return_value = ({}, True)
        with patch("diamond.chat.state_snapshot", return_value="empty"), \
             patch("builtins.input", side_effect=["/status", "/quit"]), \
             patch("diamond.shell_commands._chat") as chat, redirect_stdout(io.StringIO()):
            cmd_chat(shell, [])
        shell._run_cmd.assert_called_once_with(["--json", "project", "status"])
        chat.assert_not_called()
