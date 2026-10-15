"""Tmux-backed session adapter for launching and managing CLI agent sessions.

Provides tmux session/pane lifecycle management for isolated agent execution.
"""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ValidationError
from ..validation import text


@dataclass(frozen=True)
class TmuxSessionInfo:
    """Information about a tmux session."""
    session_name: str
    pane_id: str
    pid: int | None = None
    working_directory: str | None = None


class SessionAdapterError(Exception):
    """Session adapter operation error."""
    pass


class TmuxNotAvailableError(SessionAdapterError):
    """tmux is not available or not functional."""
    pass


class SessionAdapter:
    """Manages tmux sessions for CLI agent isolation."""

    def __init__(
        self,
        tmux_executable: str = "tmux",
        base_session_prefix: str = "diamond-agent",
        default_shell: str | None = None,
    ) -> None:
        self.tmux_executable = tmux_executable
        self.base_session_prefix = base_session_prefix
        self.default_shell = default_shell or os.environ.get("SHELL", "/bin/bash")

        # Verify tmux is available
        self._verify_tmux()

    def _verify_tmux(self) -> None:
        """Verify tmux is available and functional."""
        try:
            result = subprocess.run(
                [self.tmux_executable, "-V"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise TmuxNotAvailableError(f"tmux version check failed: {result.stderr}")
        except FileNotFoundError:
            raise TmuxNotAvailableError(f"tmux not found at {self.tmux_executable}")
        except subprocess.TimeoutExpired:
            raise TmuxNotAvailableError("tmux version check timed out")

    def _session_name(self, agent_name: str) -> str:
        """Generate tmux session name for an agent."""
        text(agent_name, "agent_name", 128)
        return f"{self.base_session_prefix}-{agent_name}"

    def _pane_target(self, session_name: str, pane_index: int = 0) -> str:
        """Generate tmux pane target string."""
        return f"{session_name}.{pane_index}"

    def create_session(
        self,
        agent_name: str,
        working_directory: Path | None = None,
        environment: dict[str, str] | None = None,
        detached: bool = True,
    ) -> TmuxSessionInfo:
        """Create a new tmux session for an agent.

        Returns session info with session name and pane ID.
        """
        session_name = self._session_name(agent_name)

        # Check if session already exists
        if self.session_exists(session_name):
            raise ValidationError(f"tmux session {session_name} already exists")

        # Build tmux new-session command
        cmd = [self.tmux_executable, "new-session"]
        if detached:
            cmd.append("-d")
        cmd.extend(["-s", session_name])

        if working_directory:
            cmd.extend(["-c", str(working_directory)])

        # Set up environment
        env = os.environ.copy()
        if environment:
            env.update(environment)

        try:
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise SessionAdapterError(f"failed to create tmux session: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise SessionAdapterError("tmux session creation timed out")

        # Get the pane ID
        pane_id = self._get_pane_id(session_name, 0)

        # Get PID of the shell process in the pane
        pid = self._get_pane_pid(session_name, 0)

        return TmuxSessionInfo(
            session_name=session_name,
            pane_id=pane_id,
            pid=pid,
            working_directory=str(working_directory) if working_directory else None,
        )

    def session_exists(self, session_name: str) -> bool:
        """Check if a tmux session exists."""
        try:
            result = subprocess.run(
                [self.tmux_executable, "has-session", "-t", session_name],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def _get_pane_id(self, session_name: str, pane_index: int = 0) -> str:
        """Get the pane ID for a session/pane."""
        try:
            result = subprocess.run(
                [
                    self.tmux_executable, "list-panes", "-t", session_name,
                    "-F", "#{pane_id}"
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return f"{session_name}.{pane_index}"
            panes = result.stdout.strip().split("\n")
            if pane_index < len(panes):
                return panes[pane_index]
            return f"{session_name}.{pane_index}"
        except subprocess.TimeoutExpired:
            return f"{session_name}.{pane_index}"

    def _get_pane_pid(self, session_name: str, pane_index: int = 0) -> int | None:
        """Get the PID of the process in a pane."""
        try:
            result = subprocess.run(
                [
                    self.tmux_executable, "display-message", "-t", self._pane_target(session_name, pane_index),
                    "-p", "#{pane_pid}"
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                pid_str = result.stdout.strip()
                if pid_str.isdigit():
                    return int(pid_str)
        except subprocess.TimeoutExpired:
            pass
        return None

    def send_keys(
        self,
        session_name: str,
        keys: str,
        pane_index: int = 0,
        enter: bool = True,
    ) -> None:
        """Send keystrokes to a tmux pane."""
        target = self._pane_target(session_name, pane_index)
        cmd = [self.tmux_executable, "send-keys", "-t", target]
        if enter:
            cmd.append(keys)
            cmd.append("Enter")
        else:
            cmd.append(keys)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise SessionAdapterError(f"send-keys failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise SessionAdapterError("send-keys timed out")

    def send_text(
        self,
        session_name: str,
        text: str,
        pane_index: int = 0,
    ) -> None:
        """Send text to a tmux pane (without auto-enter)."""
        self.send_keys(session_name, text, pane_index, enter=False)

    def send_enter(self, session_name: str, pane_index: int = 0) -> None:
        """Send Enter key to a tmux pane."""
        self.send_keys(session_name, "", pane_index, enter=True)

    def capture_pane(
        self,
        session_name: str,
        pane_index: int = 0,
        lines: int = 100,
    ) -> str:
        """Capture pane content."""
        target = self._pane_target(session_name, pane_index)
        try:
            result = subprocess.run(
                [
                    self.tmux_executable, "capture-pane", "-t", target,
                    "-p", "-S", f"-{lines}"
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise SessionAdapterError(f"capture-pane failed: {result.stderr}")
            return result.stdout
        except subprocess.TimeoutExpired:
            raise SessionAdapterError("capture-pane timed out")

    def wait_for_prompt(
        self,
        session_name: str,
        pane_index: int = 0,
        prompt_pattern: str = r"[$#>] ",
        timeout_seconds: int = 30,
        poll_interval: float = 0.5,
    ) -> bool:
        """Wait for a shell prompt to appear in the pane."""
        import re
        pattern = re.compile(prompt_pattern)
        start = time.monotonic()

        while time.monotonic() - start < timeout_seconds:
            content = self.capture_pane(session_name, pane_index, lines=10)
            if pattern.search(content):
                return True
            time.sleep(poll_interval)

        return False

    def kill_session(self, session_name: str) -> None:
        """Kill a tmux session."""
        if not self.session_exists(session_name):
            return

        try:
            result = subprocess.run(
                [self.tmux_executable, "kill-session", "-t", session_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise SessionAdapterError(f"kill-session failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise SessionAdapterError("kill-session timed out")

    def list_sessions(self, prefix_filter: str | None = None) -> list[str]:
        """List tmux sessions, optionally filtered by prefix."""
        try:
            result = subprocess.run(
                [self.tmux_executable, "list-sessions", "-F", "#{session_name}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return []
            sessions = result.stdout.strip().split("\n")
            if prefix_filter:
                sessions = [s for s in sessions if s.startswith(prefix_filter)]
            return sessions
        except subprocess.TimeoutExpired:
            return []

    def get_session_info(self, session_name: str) -> TmuxSessionInfo | None:
        """Get detailed info about a session."""
        if not self.session_exists(session_name):
            return None

        pane_id = self._get_pane_id(session_name, 0)
        pid = self._get_pane_pid(session_name, 0)

        # Get working directory from pane
        working_directory = None
        try:
            result = subprocess.run(
                [
                    self.tmux_executable, "display-message", "-t", self._pane_target(session_name, 0),
                    "-p", "#{pane_current_path}"
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                working_directory = result.stdout.strip()
        except subprocess.TimeoutExpired:
            pass

        return TmuxSessionInfo(
            session_name=session_name,
            pane_id=pane_id,
            pid=pid,
            working_directory=working_directory,
        )

    def resize_pane(
        self,
        session_name: str,
        pane_index: int = 0,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        """Resize a tmux pane."""
        target = self._pane_target(session_name, pane_index)
        cmd = [self.tmux_executable, "resize-pane", "-t", target]
        if width is not None:
            cmd.extend(["-x", str(width)])
        if height is not None:
            cmd.extend(["-y", str(height)])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise SessionAdapterError(f"resize-pane failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            raise SessionAdapterError("resize-pane timed out")

    def split_pane(
        self,
        session_name: str,
        pane_index: int = 0,
        direction: str = "horizontal",
        percentage: int = 50,
    ) -> str:
        """Split a pane and return the new pane ID."""
        target = self._pane_target(session_name, pane_index)
        flag = "-h" if direction == "horizontal" else "-v"
        try:
            result = subprocess.run(
                [
                    self.tmux_executable, "split-window", flag,
                    "-t", target,
                    "-p", str(percentage),
                    "-F", "#{pane_id}"
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise SessionAdapterError(f"split-window failed: {result.stderr}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise SessionAdapterError("split-window timed out")