import os
import shutil
import tempfile
from pathlib import Path

from .errors import ValidationError


def executable() -> str | None:
    """Return macOS's host sandbox executable when the capability exists."""
    if os.uname().sysname != "Darwin":
        return None
    return shutil.which("sandbox-exec")


def _profile_path(path: Path) -> str:
    value = str(path.resolve())
    return value.replace("\\", "\\\\").replace('"', '\\"')


def read_only_profile(workspace: Path, runtime_dir: Path, provider: str = "omnirush",
                      owned_paths: tuple[str, ...] = ()) -> str:
    if not workspace.is_dir() or not runtime_dir.is_absolute():
        raise ValidationError("sandbox profile paths must be valid directories/absolute paths")
    workspace_path = _profile_path(workspace)
    runtime_path = _profile_path(runtime_dir)
    state_paths = {
        "omnirush": (".omnirush", ".config/omnirush", ".cache/omnirush"),
        "claude": (".claude", ".cache/claude", ".config/claude"),
        "codex": (".codex",), "copilot": (".copilot",),
        "gemini": (".gemini",), "antigravity": (".gemini/antigravity-cli", ".antigravity", ".config/agy", ".agy"),
    }
    if provider not in state_paths:
        raise ValidationError("unknown sandbox provider")
    provider_paths = "\n".join(
        f'(allow file-write* (subpath "{_profile_path(path)}"))'
        for path in (Path.home() / name for name in state_paths[provider])
    )
    grants = []
    root = workspace.resolve(strict=True)
    for relative in owned_paths:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValidationError("sandbox owned paths must be contained relative paths")
        target = (root / path).resolve()
        if not target.is_relative_to(root) or target == root:
            raise ValidationError("sandbox owned path escapes or grants the whole workspace")
        selector = "subpath" if target.is_dir() else "literal"
        grants.append(f'(allow file-write* ({selector} "{_profile_path(target)}"))')
    if provider == "claude":
        for name in (".claude.json", ".claude.json.lock"):
            grants.append(f'(allow file-write* (literal "{_profile_path(Path.home() / name)}"))')
    owned_grants = "\n".join(grants)
    loopback = '''(allow network-bind (local ip "localhost:*"))
(allow network-inbound (local ip "localhost:*"))''' if provider == "antigravity" else ""
    return f'''(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow file-read*)
(allow file-write* (subpath "{runtime_path}"))
(allow file-write* (subpath "{workspace_path}/.claude-flow"))
{provider_paths}
{owned_grants}
(allow file-write* (literal "/dev/null"))
(allow sysctl-read)
(allow network-outbound)
{loopback}
(allow mach-lookup)
(allow ipc-posix-shm*)'''


def prepare_read_only(workspace: Path, argv: tuple[str, ...], environment: dict[str, str],
                      provider: str = "omnirush", owned_paths: tuple[str, ...] = ()) -> tuple[tuple[str, ...], Path]:
    sandbox = executable()
    if sandbox is None:
        raise ValidationError("macOS sandbox-exec is unavailable; packet write policy cannot be enforced")
    parent = Path(environment.get("TMPDIR") or tempfile.gettempdir())
    if not parent.is_dir():
        raise ValidationError(f"sandbox temporary parent is unavailable: {parent}")
    runtime_dir = Path(tempfile.mkdtemp(prefix="diamond-sandbox-", dir=parent))
    try:
        profile = read_only_profile(workspace, runtime_dir, provider, owned_paths)
    except Exception:
        shutil.rmtree(runtime_dir, ignore_errors=True)
        raise
    environment["TMPDIR"] = str(runtime_dir)
    environment["XDG_CACHE_HOME"] = str(runtime_dir)
    environment["XDG_STATE_HOME"] = str(runtime_dir)
    return (sandbox, "-p", profile, *argv), runtime_dir
