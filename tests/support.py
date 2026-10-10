import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CONFIG_TEXT = (ROOT / "config" / "diamond.toml").read_text(encoding="utf-8")
TASK_ID = "a" * 32
OTHER_ID = "b" * 32


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        safe_temp = Path(os.environ.get("DIAMOND_TEST_TMPDIR") or os.environ.get("TMPDIR") or tempfile.gettempdir())
        if not safe_temp.is_dir():
            self.fail(f"test temporary parent must already exist: {safe_temp}")
        self.temporary = tempfile.TemporaryDirectory(prefix="diamond-test-", dir=safe_temp)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / "project with spaces"
        (self.project / "config").mkdir(parents=True)
        (self.project / "config" / "diamond.toml").write_text(CONFIG_TEXT, encoding="utf-8")
        self.environment = patch.dict(os.environ, {"TMPDIR": str(safe_temp), "PYTHONDONTWRITEBYTECODE": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def fake_executable(self, name: str, body: str) -> Path:
        executable = self.base / name
        executable.write_text(f"#!{sys.executable}\n" + body + "\n", encoding="utf-8")
        executable.chmod(0o755)
        return executable

    def cli_process(self, *arguments: str, environment: dict[str, str] | None = None,
                    cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
        env.update(environment or {})
        return subprocess.run([sys.executable, "-B", "-m", "diamond", "--project", str(self.project), *arguments],
                              cwd=cwd or self.base, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=15)
