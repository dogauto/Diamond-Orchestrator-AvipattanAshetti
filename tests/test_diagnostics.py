import errno
import json
import selectors
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch

from diamond.config import ProviderConfig, parse_config
from diamond.diagnostics import doctor, probe_version
from diamond.domain import Provider
from diamond.errors import ValidationError
from support import CONFIG_TEXT, ProjectCase


class DiagnosticTests(ProjectCase):
    def wait_for_path(self, path: Path, message: str, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), message)

    def started_probe(self, name: str) -> tuple[Path, list[subprocess.Popen[bytes]], Callable[..., subprocess.Popen[bytes]]]:
        ready = self.base / f"{name}-started"
        executable = self.fake_executable(name, f"import pathlib,signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\npathlib.Path({str(ready)!r}).write_text('started')\nprint('fake 1.2.3', flush=True)\ntime.sleep(30)")
        real_popen = subprocess.Popen
        launched: list[subprocess.Popen[bytes]] = []

        def launch(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.append(process)
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "fake process must start before injected failure")
            self.assertIsNone(process.poll())
            return process

        def cleanup() -> None:
            for process in launched:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

        self.addCleanup(cleanup)
        return executable, launched, launch

    def assert_reaped_and_closed(self, process: subprocess.Popen[bytes]) -> None:
        self.assertIsNotNone(process.returncode)
        self.assertEqual(process.wait(timeout=0), process.returncode)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_selector_creation_failure_after_launch_reaps_and_closes(self) -> None:
        executable, launched, launch = self.started_probe("selector-construction")
        with patch("diamond.diagnostics.subprocess.Popen", side_effect=launch), \
             patch("diamond.diagnostics.selectors.DefaultSelector", side_effect=OSError(errno.EMFILE, "injected descriptor exhaustion")):
            result = probe_version(Provider.CLAUDE, str(executable), timeout_seconds=0.1, output_limit=32)
        self.assertEqual((result.status, result.detail), ("invalid_output", "capture_failed"))
        self.assert_reaped_and_closed(launched[0])
        self.assertLessEqual(len(result.stdout) + len(result.stderr), 32)

    def test_selector_close_failure_cannot_skip_process_or_pipe_cleanup(self) -> None:
        executable, launched, launch = self.started_probe("selector-close")
        selector = selectors.DefaultSelector()
        self.addCleanup(selector.close)
        started = time.monotonic()
        with patch("diamond.diagnostics.subprocess.Popen", side_effect=launch), \
             patch("diamond.diagnostics.selectors.DefaultSelector", return_value=selector), \
             patch.object(selector, "close", side_effect=OSError("injected selector close failure")):
            result = probe_version(Provider.CLAUDE, str(executable), timeout_seconds=0.1, output_limit=32)
        self.assertEqual((result.status, result.detail), ("invalid_output", "selector_close_failed"))
        self.assert_reaped_and_closed(launched[0])
        self.assertLess(time.monotonic() - started, 5)
        self.assertLessEqual(len(result.stdout) + len(result.stderr), 32)

    def test_capture_interruption_propagates_after_process_and_pipe_cleanup(self) -> None:
        executable, launched, launch = self.started_probe("capture-interrupt")
        with patch("diamond.diagnostics.subprocess.Popen", side_effect=launch), \
             patch("diamond.diagnostics.selectors.DefaultSelector", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                probe_version(Provider.CLAUDE, str(executable), timeout_seconds=0.1)
        self.assert_reaped_and_closed(launched[0])

    def test_interrupt_during_cleanup_wait_still_forces_reap_and_closes(self) -> None:
        from diamond.diagnostics import _capture

        executable, launched, launch = self.started_probe("cleanup-wait-interrupt")
        process = launch([str(executable), "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        wait = process.wait
        calls = 0

        def interrupted_wait(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt
            return wait(*args, **kwargs)

        with patch.object(process, "wait", side_effect=interrupted_wait), self.assertRaises(KeyboardInterrupt):
            _capture(process, 0.1, 32)
        self.assert_reaped_and_closed(launched[0])

    def test_capture_failure_keeps_bounded_partial_output_and_releases_resources(self) -> None:
        executable, launched, launch = self.started_probe("capture-partial-failure")
        selector = selectors.DefaultSelector()
        self.addCleanup(selector.close)
        select = selector.select
        captured = False

        def failing_select(timeout):
            nonlocal captured
            if captured:
                raise OSError(errno.EIO, "injected capture error")
            events = select(timeout)
            captured = bool(events)
            return events

        with patch("diamond.diagnostics.subprocess.Popen", side_effect=launch), \
             patch("diamond.diagnostics.selectors.DefaultSelector", return_value=selector), \
             patch.object(selector, "select", side_effect=failing_select):
            result = probe_version(Provider.CLAUDE, str(executable), timeout_seconds=0.5, output_limit=32)
        self.assertEqual((result.status, result.detail), ("invalid_output", "capture_failed"))
        self.assertIn("1.2.3", result.stdout)
        self.assertLessEqual(len(result.stdout) + len(result.stderr), 32)
        self.assert_reaped_and_closed(launched[0])

    def test_pipe_close_error_and_cleanup_interruption_release_remaining_resources(self) -> None:
        from diamond.diagnostics import _capture

        for failure in (OSError("injected pipe close error"), KeyboardInterrupt()):
            executable, launched, launch = self.started_probe(f"pipe-failure-{type(failure).__name__}")
            process = launch([str(executable), "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, start_new_session=True)
            close = process.stdout.close

            def faulty_close():
                close()
                raise failure

            with patch.object(process.stdout, "close", side_effect=faulty_close):
                if isinstance(failure, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        _capture(process, 0.1, 32)
                else:
                    self.assertEqual(_capture(process, 0.1, 32)[2], "pipe_close_failed")
            self.assert_reaped_and_closed(launched[0])

    def test_fake_success_path_spaces_safe_argv_and_notice(self) -> None:
        executable = self.fake_executable("fake CLI with spaces", "import sys\nassert sys.argv[1:] == ['--version']\nprint('GitHub Copilot CLI 1.0.91.')\nprint(\"Run copilot update to check for updates.\")")
        real_popen = subprocess.Popen
        with patch("diamond.diagnostics.subprocess.Popen", wraps=real_popen) as launch:
            result = probe_version(Provider.COPILOT, str(executable))
        self.assertEqual((result.status, result.version, result.auth_status), ("available", "1.0.91", "NOT_CHECKED"))
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0], [str(executable), "--version"])
        self.assertIs(launch.call_args.kwargs["shell"], False)
        self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        json.dumps(result.to_dict(), allow_nan=False)

    def test_missing_executable_never_launches(self) -> None:
        with patch("diamond.diagnostics.shutil.which", return_value=None), patch("subprocess.Popen", side_effect=AssertionError("must not launch")):
            result = probe_version(Provider.GEMINI, "not-installed")
        self.assertEqual(result.status, "missing")
        self.assertIsNone(result.version)

    def test_fake_nonzero_stderr_and_invalid_output(self) -> None:
        executable = self.fake_executable("nonzero", "import sys\nprint('failed', file=sys.stderr)\nsys.exit(7)")
        result = probe_version(Provider.CLAUDE, str(executable))
        self.assertEqual((result.status, result.returncode), ("nonzero", 7))
        self.assertIn("failed", result.stderr)
        invalid = self.fake_executable("invalid", "print('not a version')")
        self.assertEqual(probe_version(Provider.CODEX, str(invalid)).status, "invalid_output")
        stderr_version = self.fake_executable("stderr-version", "import sys\nprint('v2.1.3-beta', file=sys.stderr)")
        self.assertEqual(probe_version(Provider.OMNIRUSH, str(stderr_version)).version, "2.1.3-beta")

    def test_fake_timeout_and_output_limit_are_bounded_and_reaped(self) -> None:
        sleeping = self.fake_executable("sleeper", "import time\ntime.sleep(30)")
        start = time.monotonic()
        result = probe_version(Provider.CLAUDE, str(sleeping), timeout_seconds=0.1)
        self.assertEqual(result.status, "timeout")
        self.assertLess(time.monotonic() - start, 3)
        noisy = self.fake_executable("noisy", "import sys\nsys.stdout.write('x' * 1000000)\nsys.stdout.flush()")
        result = probe_version(Provider.CLAUDE, str(noisy), output_limit=128)
        self.assertEqual((result.status, result.detail), ("invalid_output", "output_limit"))
        self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 128)
        self.assertIsNotNone(result.returncode)

    def test_children_cleaned_after_timeout_and_successful_parent_exit(self) -> None:
        real_popen = subprocess.Popen
        for exits in (False, True):
            sentinel = self.base / f"child-survived-{exits}"
            ready = self.base / f"child-started-{exits}"
            release = self.base / f"child-release-{exits}"
            child = ("import time,pathlib,signal\n"
                     "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                     f"pathlib.Path({str(ready)!r}).write_text('started')\n"
                     f"release=pathlib.Path({str(release)!r})\n"
                     "while not release.exists():\n"
                     "    time.sleep(0.01)\n"
                     f"pathlib.Path({str(sentinel)!r}).write_text('survived')")
            body = (f"import subprocess,sys,time,pathlib\n"
                    f"subprocess.Popen([sys.executable,'-c',{child!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                    f"print('fake 1.2.3', flush=True)\n"
                    f"while not pathlib.Path({str(ready)!r}).exists(): time.sleep(0.01)\n")
            if not exits:
                body += "time.sleep(30)\n"
            executable = self.fake_executable(f"child-parent-{exits}", body)
            launched = []

            def launch(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                launched.append(process)
                self.wait_for_path(ready, "child must be ready before capture deadline starts")
                return process

            with patch("diamond.diagnostics.subprocess.Popen", side_effect=launch):
                result = probe_version(Provider.GEMINI, str(executable), timeout_seconds=1.5 if exits else 0.8)
            self.assertEqual(result.status, "available" if exits else "timeout", result.to_dict())
            self.assertTrue(ready.exists(), "child must actually start before cleanup is tested")
            self.assert_reaped_and_closed(launched[0])
            release.write_text("release", encoding="utf-8")
            deadline = time.monotonic() + 1.0
            while not sentinel.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(sentinel.exists(), "version probe left a child running")

    def test_mocked_outcomes_and_execution_errors(self) -> None:
        cases = [(b"tool 1.2.3", b"", "", 0, "available"),
                 (b"", b"error", "", 4, "nonzero"),
                 (b"", b"", "timeout", -9, "timeout"),
                 (b"bad", b"", "", 0, "invalid_output"),
                 (b"", b"", "cleanup_failed", -9, "invalid_output")]
        for stdout, stderr, outcome, returncode, expected in cases:
            with self.subTest(expected=expected), patch("diamond.diagnostics.shutil.which", return_value="/fake/tool"), \
                 patch("diamond.diagnostics.subprocess.Popen", return_value=SimpleNamespace(returncode=returncode)), \
                 patch("diamond.diagnostics._capture", return_value=(stdout, stderr, outcome)):
                self.assertEqual(probe_version(Provider.CLAUDE, "tool").status, expected)
        for exception, expected in ((FileNotFoundError("gone"), "missing"), (PermissionError("denied"), "nonzero")):
            with patch("diamond.diagnostics.shutil.which", return_value="/fake/tool"), patch("subprocess.Popen", side_effect=exception):
                self.assertEqual(probe_version(Provider.CLAUDE, "tool").status, expected)

    def test_doctor_reports_all_five_and_skips_disabled(self) -> None:
        source = CONFIG_TEXT.replace('enabled = true', 'enabled = false', 1)
        source = source.replace('planner = "gemini"', 'planner = "claude"').replace('researcher = "gemini"', 'researcher = "claude"')
        config = parse_config(source)
        with patch("diamond.diagnostics.probe_version", side_effect=lambda provider, executable: SimpleNamespace(provider=provider, status="available")) as probe:
            results = doctor(config)
        self.assertEqual(len(results), 6)
        self.assertEqual(results[0].status, "disabled")
        self.assertEqual(probe.call_count, 5)
        self.assertNotIn(Provider.GEMINI, [call.args[0] for call in probe.call_args_list])

    def test_oversized_version_is_invalid_output_not_invalid_request(self) -> None:
        executable = self.fake_executable("oversized-version", "import sys\nassert sys.argv[1:] == ['--version']\nprint('1.2.' + '3' * 5000)")
        result = probe_version(Provider.GEMINI, str(executable))
        self.assertEqual((result.status, result.returncode), ("invalid_output", 0))
        self.assertIsNone(result.version)
        self.assertIn("format/length", result.detail)
        self.assertLess(len(result.stdout.encode()), 65_536)

    def test_doctor_continues_all_five_after_oversized_version(self) -> None:
        oversized = self.fake_executable("oversized-version", "print('1.2.' + '3' * 5000)")
        valid = self.fake_executable("valid version", "import sys\nassert sys.argv[1:] == ['--version']\nprint('fake 1.2.3')")
        config = parse_config(CONFIG_TEXT)
        providers = {provider: ProviderConfig(str(oversized if provider is Provider.GEMINI else valid), True) for provider in Provider}
        results = doctor(replace(config, providers=providers))
        self.assertEqual([result.status for result in results], ["invalid_output"] + ["available"] * 5)
        self.assertTrue(all(result.auth_status == "NOT_CHECKED" for result in results))
        json.dumps([result.to_dict() for result in results], allow_nan=False)

    def test_invalid_probe_limits_rejected_before_process(self) -> None:
        with patch("subprocess.Popen", side_effect=AssertionError("must not launch")):
            for timeout in (True, 0, -1, float("nan"), float("inf"), 31):
                with self.subTest(timeout=timeout), self.assertRaises(ValidationError):
                    probe_version(Provider.CLAUDE, "claude", timeout_seconds=timeout)
            with self.assertRaises(ValidationError):
                probe_version(Provider.CLAUDE, "claude", output_limit=True)
