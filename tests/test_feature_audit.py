import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

from diamond.chat import _extract_text, _fetch_sse
from diamond.cli import parser
from diamond.errors import ConflictError, ValidationError
from diamond.mcp_server import serve_stdio
from diamond.paths import resolve_paths
from diamond.self_improve import validate_owned_paths
from diamond.self_improve import publish_verified_changes
from diamond.shell import DiamondShell
from support import ProjectCase


class FeatureAuditTests(ProjectCase):
    def improvement_fixture(self) -> tuple[Path, list[str]]:
        source = self.project / "src/diamond/feature.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n")
        tests = self.project / "tests"
        tests.mkdir()
        (tests / "check.py").write_text("assert True\n")
        state = self.base / "improvement-state"
        args = ["--state-dir", str(state), "--json", "self-improve", "create",
                "--objective", "Audit bounded improvement", "--owned-path", "src/diamond/feature.py",
                "--test", json.dumps({"name": "fixture", "argv": [sys.executable, "tests/check.py"],
                                      "timeout_seconds": 5, "output_limit": 4096}),
                "--command-id", "audit-improvement"]
        return source, args

    def test_self_improvement_retry_preserves_original_checkpoint(self) -> None:
        source, args = self.improvement_fixture()
        first = self.cli_process(*args)
        self.assertEqual(first.returncode, 0, first.stdout)
        result = json.loads(first.stdout)["result"]
        saved = Path(result["checkpoint"]) / "src/diamond/feature.py"
        source.write_text("value = 2\n")
        retry = self.cli_process(*args)
        self.assertEqual(retry.returncode, 0, retry.stdout)
        self.assertEqual(saved.read_text(), "value = 1\n")
        self.assertEqual(json.loads(retry.stdout)["result"]["workflow"], result["workflow"])

    def test_self_improvement_invalid_id_cannot_create_external_checkpoint(self) -> None:
        _, args = self.improvement_fixture()
        escaped = self.base / "escaped-checkpoint"
        response = self.cli_process(*args, "--workflow-id", str(escaped))
        self.assertNotEqual(response.returncode, 0)
        self.assertFalse(escaped.exists())
        self.assertEqual(json.loads(response.stdout)["error"]["code"], "INVALID_REQUEST")

    def test_self_improvement_symlink_alias_cannot_own_protected_source(self) -> None:
        source, _ = self.improvement_fixture()
        protected = source.parent / "store.py"
        protected.write_text("value = 1\n")
        alias = source.parent / "alias.py"
        alias.symlink_to(protected)
        with self.assertRaises(ValidationError):
            validate_owned_paths(self.project, ("src/diamond/alias.py",))

    def test_incomplete_rollback_does_not_restore_any_files(self) -> None:
        source, args = self.improvement_fixture()
        other = source.parent / "other.py"
        other.write_text("original = True\n")
        created = self.cli_process(*args, "--owned-path", "src/diamond/other.py")
        self.assertEqual(created.returncode, 0, created.stdout)
        result = json.loads(created.stdout)["result"]
        (Path(result["checkpoint"]) / "src/diamond/other.py").unlink()
        source.write_text("human_change = True\n")
        rollback = self.cli_process(*args[:3], "--json", "self-improve", "rollback",
                                    result["workflow"]["spec"]["workflow_id"])
        self.assertNotEqual(rollback.returncode, 0)
        self.assertEqual(source.read_text(), "human_change = True\n")

    def test_verified_publication_rejects_human_change_without_overwrite(self) -> None:
        source, args = self.improvement_fixture()
        created = self.cli_process(*args)
        self.assertEqual(created.returncode, 0, created.stdout)
        result = json.loads(created.stdout)["result"]
        checkpoint = Path(result["checkpoint"])
        source.write_text("human_change = True\n")
        with self.assertRaises(ConflictError):
            publish_verified_changes(self.project, checkpoint, ("src/diamond/feature.py",))
        self.assertEqual(source.read_text(), "human_change = True\n")

    def test_shell_explicit_state_directory_is_used_for_mutation(self) -> None:
        state = self.base / "shell-state"
        shell = DiamondShell(str(self.project), state_dir=str(state))
        result, ok = shell._run_cmd(["task", "create", "--objective", "isolated shell"])
        self.assertTrue(ok, result)
        self.assertTrue((state / "diamond.sqlite3").exists())
        self.assertFalse((self.project / ".diamond").exists())

    def test_mcp_deep_json_returns_parse_error_and_keeps_transport_alive(self) -> None:
        incoming = io.StringIO("[" * 2000 + "]" * 2000 + '\n' +
                               json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + '\n')
        outgoing = io.StringIO()
        paths = resolve_paths(str(self.project), None, str(self.base / "mcp-state"))
        self.assertEqual(serve_stdio(paths, incoming, outgoing), 0)
        responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(responses[0]["error"]["code"], (-32700, -32600))
        self.assertEqual(responses[1]["id"], 2)

    def test_chat_completed_message_and_streamed_delta_are_readable(self) -> None:
        for content in ({"delta": {"content": "hello"}}, {"message": {"content": "hello"}}):
            with self.subTest(content=content):
                self.assertEqual(_extract_text([{"choices": [content]}]), "hello")

    def test_chat_response_is_closed_and_done_terminates_stream(self) -> None:
        response = io.BytesIO(b'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"hello"}}]}\n'
                              b'data: [DONE]\n'
                              b'data: {"object":"chat.completion.chunk","choices":[{"delta":{"content":"unexpected"}}]}\n')
        with patch("diamond.chat.urllib.request.urlopen", return_value=response):
            chunks = _fetch_sse("http://127.0.0.1:31415/v1/chat/completions", {}, "fixture")
        self.assertTrue(response.closed)
        self.assertEqual(_extract_text(chunks), "hello")

    def test_native_console_workflow_aliases_accept_trailing_json(self) -> None:
        for family in ("workflow", "delivery"):
            for action in ("run", "resume"):
                with self.subTest(family=family, action=action):
                    args = parser().parse_args([family, action, "a" * 32, "--expected-revision", "1", "--json"])
                    self.assertTrue(args.json)

    def test_mcp_oversize_line_is_drained_with_bounded_reads(self) -> None:
        from diamond.mcp_server import MAX_MESSAGE_BYTES

        class BoundedInput(io.StringIO):
            def readline(self, size: int = -1) -> str:
                if not 0 < size <= MAX_MESSAGE_BYTES + 1:
                    raise AssertionError("unbounded read")
                return super().readline(size)

        incoming = BoundedInput("x" * (MAX_MESSAGE_BYTES * 2) + '\n' +
                                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + '\n')
        outgoing = io.StringIO()
        paths = resolve_paths(str(self.project), None, str(self.base / "state"))
        serve_stdio(paths, incoming, outgoing)
        responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 2)
