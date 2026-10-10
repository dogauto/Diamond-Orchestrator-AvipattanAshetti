import os
import subprocess
from dataclasses import dataclass

from .errors import ValidationError
from .validation import bounded_int, text


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    pgid: int
    fingerprint: str

    def __post_init__(self) -> None:
        bounded_int(self.pid, "process pid", 1, 2**31 - 1)
        bounded_int(self.pgid, "process pgid", 1, 2**31 - 1)
        text(self.fingerprint, "process fingerprint", 1024)

    def to_dict(self) -> dict[str, object]:
        return {"pid": self.pid, "pgid": self.pgid, "fingerprint": self.fingerprint}


def identify(pid: int) -> ProcessIdentity | None:
    bounded_int(pid, "process pid", 1, 2**31 - 1)
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "pgid=", "-o", "lstart=", "-o", "uid=", "-o", "comm="],
                            capture_output=True, text=True, timeout=2,
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    if result.returncode == 1 and not result.stdout.strip():
        return None
    if result.returncode != 0 or len(result.stdout) > 2048:
        raise ValidationError("process identity probe failed")
    values = result.stdout.strip().split(maxsplit=1)
    if len(values) != 2 or not values[0].isdigit():
        raise ValidationError("process identity probe returned invalid fields")
    return ProcessIdentity(pid, int(values[0]), values[1])


def observe(identity: ProcessIdentity | None) -> dict[str, object]:
    """Observation only: a restart never signals a PID using a coarse timestamp."""
    if identity is None:
        return {"classification": "UNKNOWN_PROCESS", "relaunch_allowed": False}
    try:
        current = identify(identity.pid)
    except (OSError, subprocess.SubprocessError, ValidationError):
        classification = "PROBE_FAILED"
    else:
        classification = "ABSENT" if current is None else (
            "IDENTITY_MATCH" if current == identity else "PID_REUSED")
    return {"classification": classification, "identity": identity.to_dict(),
            "relaunch_allowed": False, "signalled": False,
            "controller_pid": os.getpid()}
