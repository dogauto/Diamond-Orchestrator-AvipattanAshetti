import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import PropertyMock, patch

from diamond.adapters import Invocation, OutputMode, PermissionMode, build_invocation
from diamond.domain import Provider
from diamond.sandbox import executable as sandbox_executable
from diamond.supervisor import CancelledExecution, ExecutionStatus, _read_stream, _terminate, run
from diamond.errors import ValidationError
from support import ProjectCase


class AdapterTests(ProjectCase):
    def test_provider_argv_is_explicit_and_provider_specific(self) -> None:
        expected = {
            Provider.GEMINI: ("gemini", OutputMode.JSON),
            Provider.ANTIGRAVITY: ("agy", OutputMode.JSON),
            Provider.CODEX: ("codex", OutputMode.JSON),
            Provider.OMNIRUSH: ("omnirush", OutputMode.TEXT),
            Provider.COPILOT: ("copilot", OutputMode.JSON),
            Provider.CLAUDE: ("claude", OutputMode.JSON),
        }
        for provider, (binary, output_mode) in expected.items():
            invocation = build_invocation(provider, binary, "inspect this", requested_model="model-x")
            self.assertEqual(invocation.output_mode, output_mode)
            self.assertEqual(invocation.argv[0], binary)
            self.assertIn("inspect this", invocation.argv)
            self.assertNotIn("shell", invocation.argv)
            self.assertEqual(invocation.permission_enforced,
                             provider is not Provider.OMNIRUSH or sandbox_executable() is not None)
        self.assertIn("--permission-mode", build_invocation(
            Provider.CLAUDE, "claude", "plan", permission_mode=PermissionMode.PLAN).argv)
        gemini = build_invocation(Provider.GEMINI, "gemini", "plan", permission_mode=PermissionMode.PLAN).argv
        self.assertIn(("--approval-mode", "plan"), tuple(zip(gemini, gemini[1:])))
        self.assertIn("--skip-trust", gemini)
        antigravity = build_invocation(Provider.ANTIGRAVITY, "agy", "plan").argv
        self.assertIn("--mode=plan", antigravity)
        self.assertIn("--sandbox", antigravity)
        streamed = build_invocation(
            Provider.ANTIGRAVITY, "agy", "plan", stream=True,
            json_schema='{"type":"object"}')
        self.assertEqual(streamed.output_mode, OutputMode.STREAM_JSON)
        self.assertIn("stream-json", streamed.argv)
        self.assertIn("--json-schema", streamed.argv)
        codex = build_invocation(Provider.CODEX, "codex", "plan").argv
        self.assertIn("--skip-git-repo-check", codex)
        self.assertIn("--ignore-user-config", codex)
        self.assertIn("--ignore-rules", codex)
        self.assertIn("--sandbox", codex)
        self.assertIn("read-only", codex)
        self.assertNotIn("--model", codex)
        selected = build_invocation(Provider.CODEX, "codex", "plan", requested_model="o4-mini").argv
        self.assertIn(("--model", "o4-mini"), tuple(zip(selected, selected[1:])))
        codex_schema = build_invocation(Provider.CODEX, "codex", "review",
                                        json_schema_path="/tmp/delivery-schema.json").argv
        self.assertIn(("--output-schema", "/tmp/delivery-schema.json"), tuple(zip(codex_schema, codex_schema[1:])))
        with self.assertRaises(ValidationError):
            build_invocation(Provider.CODEX, "codex", "review", json_schema='{"type":"object"}')
        with self.assertRaises(ValidationError):
            Invocation(Provider.CODEX, ("codex",), OutputMode.JSON, None, json_schema='{"type":"object"}')
        copilot = build_invocation(Provider.COPILOT, "copilot", "plan").argv
        self.assertIn(("--mode", "plan"), tuple(zip(copilot, copilot[1:])))

    def test_omnirush_plan_invocation_requires_host_sandbox(self) -> None:
        invocation = build_invocation(Provider.OMNIRUSH, "omnirush", "plan")
        self.assertEqual(invocation.permission_mode, PermissionMode.PLAN)
        self.assertEqual(invocation.permission_enforced, sandbox_executable() is not None)

    def test_omnirush_plan_sandbox_blocks_workspace_write(self) -> None:
        executable = self.fake(
            "omnirush write probe",
            "from pathlib import Path\n"
            "try:\n Path('unauthorized.txt').write_text('blocked')\n print('write_allowed')\n"
            "except PermissionError:\n print('write_blocked')",
        )
        result = asyncio.run(run(
            build_invocation(Provider.OMNIRUSH, str(executable), "prompt"),
            self.project, timeout_seconds=2, output_limit=256))
        if sandbox_executable() is None:
            self.skipTest("macOS sandbox-exec is unavailable")
        self.assertEqual(result.status, ExecutionStatus.SUCCEEDED)
        self.assertEqual(result.stdout.strip(), "write_blocked")
        self.assertFalse((self.project / "unauthorized.txt").exists())

    def test_invalid_invocation_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            build_invocation(Provider.GEMINI, "gemini", "")
        with self.assertRaises(ValidationError):
            build_invocation(Provider.GEMINI, "gemini", "prompt", permission_mode="plan")  # type: ignore[arg-type]

    def test_claude_prompt_is_separated_from_variadic_mcp_arguments(self) -> None:
        invocation = build_invocation(Provider.CLAUDE, "claude", "marker", provider_tools=())
        self.assertEqual(invocation.argv[-2:], ("--", "marker"))
        self.assertIn(("--tools", ""), tuple(zip(invocation.argv, invocation.argv[1:])))
        self.assertIn(("--mcp-config", '{"mcpServers":{}}'), tuple(zip(invocation.argv, invocation.argv[1:])))
        self.assertIn("--safe-mode", invocation.argv)
        direct = build_invocation(Provider.CLAUDE, "claude", "code", provider_tools=(),
                                   permission_mode=PermissionMode.DEFAULT, json_schema='{"type":"object"}')
        self.assertIn(("--permission-mode", "dontAsk"), tuple(zip(direct.argv, direct.argv[1:])))
        self.assertIn("--json-schema", direct.argv)

    def test_antigravity_host_sandbox_allows_loopback_listener_and_blocks_workspace_write(self) -> None:
        executable = self.fake("agy listener probe", "import socket\ns=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(1); s.close()\n"
                              "from pathlib import Path\ntry: Path('unauthorized.txt').write_text('bad')\n"
                              "except PermissionError: print('loopback_allowed_write_blocked')")
        result = asyncio.run(run(build_invocation(Provider.ANTIGRAVITY, str(executable), "prompt"), self.project,
                                  timeout_seconds=3, enforce_paths=True))
        self.assertEqual(result.status, ExecutionStatus.SUCCEEDED, result.stderr)
        self.assertEqual(result.stdout.strip(), "loopback_allowed_write_blocked")
        self.assertFalse((self.project / "unauthorized.txt").exists())

    def fake(self, name: str, body: str) -> Path:
        return self.fake_executable(name, f"import sys,time\n{body}\n")

    def test_supervisor_success_nonzero_and_json_preservation(self) -> None:
        executable = self.fake("supervisor success", "print('{\\\"ok\\\":true}', flush=True)")
        invocation = build_invocation(Provider.OMNIRUSH, str(executable), "prompt")
        result = asyncio.run(run(invocation, self.project, timeout_seconds=2, output_limit=128))
        self.assertEqual(result.status, ExecutionStatus.SUCCEEDED)
        self.assertEqual(json.loads(result.stdout), {"ok": True})
        failure = self.fake("supervisor failure", "print('bad', file=sys.stderr); sys.exit(7)")
        result = asyncio.run(run(build_invocation(Provider.OMNIRUSH, str(failure), "prompt"), self.project, timeout_seconds=2))
        self.assertEqual((result.status, result.returncode), (ExecutionStatus.NONZERO, 7))

    def test_supervisor_timeout_and_output_limit_reap(self) -> None:
        sleeper = self.fake("supervisor sleeper", "time.sleep(30)")
        result = asyncio.run(run(build_invocation(Provider.OMNIRUSH, str(sleeper), "prompt"), self.project,
                                 timeout_seconds=0.1, output_limit=128))
        self.assertEqual(result.status, ExecutionStatus.TIMEOUT)
        noisy = self.fake("supervisor noisy", "sys.stdout.write('x' * 1000000); sys.stdout.flush()")
        result = asyncio.run(run(build_invocation(Provider.OMNIRUSH, str(noisy), "prompt"), self.project,
                                 timeout_seconds=2, output_limit=128))
        self.assertEqual(result.status, ExecutionStatus.OUTPUT_LIMIT)
        self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 128)

    def test_supervisor_applies_output_limit_across_both_streams(self) -> None:
        noisy = self.fake("supervisor noisy stderr", "sys.stdout.write('x' * 100); sys.stderr.write('y' * 100)")
        result = asyncio.run(run(build_invocation(Provider.OMNIRUSH, str(noisy), "prompt"), self.project,
                                 timeout_seconds=2, output_limit=128))
        self.assertEqual(result.status, ExecutionStatus.OUTPUT_LIMIT)
        self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 128)

    def test_exited_group_waits_for_async_returncode_notification(self) -> None:
        async def delayed_notification():
            process = await asyncio.create_subprocess_exec(sys.executable, "-B", "-c", "pass", start_new_session=True,
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await process.communicate()
            loop = asyncio.get_running_loop()
            notified_at = loop.time() + 0.05
            actual_returncode = process.returncode
            try:
                # The OS has reaped the child; emulate the pending watcher callback.
                with patch.object(type(process), "returncode", new_callable=PropertyMock) as returncode:
                    returncode.side_effect = lambda: None if loop.time() < notified_at else actual_returncode
                    self.assertTrue(await _terminate(process))
                self.assertEqual(process.returncode, 0)
            finally:
                process._transport.close()

        asyncio.run(delayed_notification())

    def test_flooded_pipe_and_ignored_term_are_cleaned_before_return(self) -> None:
        noisy = self.fake("blocked writer", "import os,signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                          "try: os.write(1, b'x' * 4000000)\nexcept BrokenPipeError: pass\ntime.sleep(30)")
        pids = []
        invocation = Invocation(Provider.CLAUDE, (str(noisy),), OutputMode.TEXT, None, False, PermissionMode.DEFAULT)
        result = asyncio.run(run(invocation, self.project, timeout_seconds=3, output_limit=128,
                                  on_started=lambda pid, identity: pids.append(pid)))
        self.assertEqual(result.status, ExecutionStatus.OUTPUT_LIMIT, result.detail)
        self.assertTrue(result.cleanup_complete)
        self.assertEqual(result.stdout_bytes, 128)
        with self.assertRaises(ProcessLookupError):
            os.kill(pids[0], 0)

    def test_repeated_cancellation_finishes_cleanup_and_keeps_partial_output(self) -> None:
        sleeper = self.fake("cancel twice", "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                            "print('started', flush=True)\ntime.sleep(30)")
        invocation = Invocation(Provider.CLAUDE, (str(sleeper),), OutputMode.TEXT, None, False, PermissionMode.DEFAULT)

        async def cancel_twice():
            pids = []
            ready = asyncio.Event()

            async def observed_reader(stream, limit, usage, lock, data):
                class ObservedStream:
                    async def read(self, count):
                        chunk = await stream.read(count)
                        if b"started" in chunk:
                            ready.set()
                        return chunk
                return await _read_stream(ObservedStream(), limit, usage, lock, data)

            with patch("diamond.supervisor._read_stream", new=observed_reader):
                worker = asyncio.create_task(run(invocation, self.project, timeout_seconds=3,
                                                 on_started=lambda pid, identity: pids.append(pid)))
                await asyncio.wait_for(ready.wait(), timeout=2)
            try:
                worker.cancel()
                await asyncio.sleep(0.03)
                worker.cancel()
                with self.assertRaises(CancelledExecution) as caught:
                    await worker
                self.assertTrue(caught.exception.result.cleanup_complete)
                self.assertEqual(caught.exception.result.stdout, "started\n")
                with self.assertRaises(ProcessLookupError):
                    os.kill(pids[0], 0)
            finally:
                if pids:
                    try:
                        os.killpg(pids[0], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

        asyncio.run(cancel_twice())

    def test_supervisor_cleans_descendants_after_success_and_timeout(self) -> None:
        for inherited_pipes in (False, True):
            body = ("import subprocess\n"
                    "child=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)'],"
                    + ("stdin=subprocess.DEVNULL" if inherited_pipes else
                       "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL")
                    + ")\nprint(child.pid, flush=True)")
            parent = self.fake(f"descendant {inherited_pipes}", body)
            invocation = Invocation(Provider.CLAUDE, (str(parent),), OutputMode.TEXT, None, False, PermissionMode.DEFAULT)
            groups = []
            try:
                result = asyncio.run(run(invocation, self.project, timeout_seconds=3,
                                         on_started=lambda pid, identity: groups.append(pid)))
                expected = ExecutionStatus.TIMEOUT if inherited_pipes else ExecutionStatus.SUCCEEDED
                self.assertEqual(result.status, expected, result.detail)
                self.assertTrue(result.cleanup_complete)
                self.assertTrue(result.stdout.strip().isdigit())
                with self.assertRaises(ProcessLookupError):
                    os.killpg(groups[0], 0)
            finally:
                for pid in groups:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
