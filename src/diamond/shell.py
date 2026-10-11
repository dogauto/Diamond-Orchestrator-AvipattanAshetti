"""Interactive shell for the Diamond orchestrator with AI chat.

Usage:
    python -m diamond shell          # starts the interactive CLI
    ./scripts/diamond.sh shell       # via project launcher
"""
import json
import os
import readline
import shlex
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from diamond.cli import parser as _cli_parser, _run
from diamond.paths import resolve_paths
from diamond.config import load_config
from diamond.workflow_store import WorkflowStore
from diamond.store import EventStore
from diamond.errors import DiamondError

from .shell_ui import _render_banner, _format_status, bold, cyan, dim
from .shell_history import load_history, save_history
from .shell_commands import _dispatch, CATALOG


class DiamondShell:
    def __init__(self, project_root: str | None = None, config_path: str | None = None,
                 state_dir: str | None = None):
        self.project_root = project_root or str(Path(__file__).resolve().parents[2])
        self.config_path = config_path
        self.state_dir = state_dir
        self._store = None

    def _get_store(self) -> EventStore:
        if self._store is None:
            paths = resolve_paths(self.project_root, self.config_path, self.state_dir)
            self._store = EventStore(paths.database)
        return self._store

    def _run_cmd(self, args: list[str]) -> tuple[dict, bool]:
        base = ["--project", self.project_root]
        if self.config_path:
            base += ["--config", self.config_path]
        if self.state_dir:
            base += ["--state-dir", self.state_dir]
        parser = _cli_parser()
        try:
            parsed = parser.parse_args(base + args)
            result, exit_code = _run(parsed)
            return result, exit_code == 0
        except DiamondError as exc:
            return {"error": {"code": exc.code, "message": str(exc)}}, False
        except SystemExit as exc:
            if hasattr(exc, "code") and exc.code != 0:
                return {"error": {"code": "USAGE", "message": f"Bad command: {args}"}}, False
            return {}, True

    def _display(self, result: dict, ok: bool, command: str) -> None:
        print(f"\n{dim('$ ' + command)}")
        text = _format_status(result, ok)
        if text:
            print(text)
        elif result:
            print(json.dumps(result, indent=2, default=str))


def main(project_root: str | None = None, config_path: str | None = None,
         state_dir: str | None = None) -> int:
    shell = DiamondShell(project_root, config_path, state_dir)

    load_history()

    proj = shell.project_root
    print(_render_banner(proj))

    prompt_str = f"{bold(cyan('diamond'))} {dim(f'[{Path(proj).name}]')} > "

    try:
        while True:
            try:
                line = input(prompt_str)
            except EOFError:
                break
            _dispatch(shell, line)
            save_history()
    except KeyboardInterrupt:
        print("\n" + dim("(Interrupted — type quit to exit)"))
        return 0
    finally:
        save_history()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
