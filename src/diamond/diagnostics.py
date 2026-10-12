import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable

from .config import DiamondConfig, ProviderConfig
from .domain import Provider
from .errors import ValidationError
from .validation import bounded_int, text


@dataclass(frozen=True)
class RateLimitInfo:
    """Rate-limit state from a provider probe."""
    remaining: int | None
    window_seconds: int | None
    reset_at: float | None          # monotonic timestamp or None
    source: str                     # e.g. "claude_usage", "inferred"

    def to_dict(self) -> dict[str, Any]:
        return {
            "remaining": self.remaining,
            "window_seconds": self.window_seconds,
            "reset_at": self.reset_at,
            "source": self.source,
        }


@dataclass(frozen=True)
class ProbeResult:
    provider: Provider
    status: str
    executable: str | None
    version: str | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    detail: str = ""
    auth_status: str = "NOT_CHECKED"

    def __post_init__(self) -> None:
        if not isinstance(self.provider, Provider) or not isinstance(self.status, str) or self.status not in {"available", "missing", "nonzero", "timeout", "invalid_output", "disabled"}:
            raise ValidationError("invalid diagnostic provider/status")
        for value, name in ((self.executable, "diagnostic executable"), (self.version, "diagnostic version")):
            if value is not None:
                text(value, name, 4096)
        if self.returncode is not None and type(self.returncode) is not int:
            raise ValidationError("diagnostic returncode must be an integer or None")
        if not all(type(value) is str for value in (self.stdout, self.stderr, self.detail)):
            raise ValidationError("diagnostic output/detail must be immutable strings")
        if self.auth_status != "NOT_CHECKED":
            raise ValidationError("version diagnostics cannot establish authentication")
        if self.status == "available" and not self.version:
            raise ValidationError("available diagnostics require a version")

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider.value, "status": self.status, "executable": self.executable,
                "version": self.version, "returncode": self.returncode, "stdout": self.stdout,
                "stderr": self.stderr, "detail": self.detail, "auth_status": self.auth_status}


def _cleanup(process: subprocess.Popen[bytes]) -> bool:
    def terminate(sig: int) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            elif sig == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass

    clean = True
    interrupted: BaseException | None = None
    for sig, timeout in ((signal.SIGTERM, 0.15), (signal.SIGKILL, 1.0)):
        try:
            terminate(sig)
        except OSError:
            clean = False
        except BaseException as exc:
            interrupted = interrupted or exc
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if sig == signal.SIGKILL:
                clean = False
        except OSError:
            clean = False
        except BaseException as exc:
            interrupted = interrupted or exc
    if interrupted is not None:
        raise interrupted
    return clean


def _close_pipe(stream: BinaryIO) -> None:
    error: BaseException | None = None
    for _ in range(2):
        try:
            stream.close()
        except BaseException as exc:
            if error is None or isinstance(error, Exception) and not isinstance(exc, Exception):
                error = exc
        if stream.closed:
            break
    if not stream.closed:
        try:
            os.close(stream.fileno())
        except BaseException as exc:
            error = error or exc
    if error is not None:
        raise error


def _capture(process: subprocess.Popen[bytes], timeout: float, limit: int) -> tuple[bytes, bytes, str]:
    buffers: list[bytearray] = []
    selector: selectors.BaseSelector | None = None
    outcome = ""
    interrupted: BaseException | None = None
    try:
        buffers = [bytearray(), bytearray()]
        selector = selectors.DefaultSelector()
        deadline = time.monotonic() + timeout
        assert process.stdout is not None and process.stderr is not None
        for index, stream in enumerate((process.stdout, process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = "timeout"
                break
            for key, _ in selector.select(min(remaining, 0.05)):
                capacity = limit - sum(len(buffer) for buffer in buffers)
                try:
                    chunk = os.read(key.fd, min(8192, capacity + 1))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    buffers[key.data].extend(chunk[:capacity])
                    if len(chunk) > capacity:
                        outcome = "output_limit"
                        break
            if outcome:
                break
        if not outcome:
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                outcome = "timeout"
    except Exception:
        outcome = "capture_failed"
    except BaseException as exc:
        interrupted = exc
    finally:
        # Every owned resource is released even if another release raises.
        releases: list[tuple[str, Callable[[], object]]] = [("cleanup_failed", lambda: _cleanup(process))]
        if selector is not None:
            releases.append(("selector_close_failed", selector.close))
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                releases.append(("pipe_close_failed", lambda stream=stream: _close_pipe(stream)))
        for failure, release in releases:
            try:
                if release() is False:
                    outcome = failure
            except Exception:
                outcome = failure
            except BaseException as exc:
                interrupted = interrupted or exc
        if interrupted is not None:
            raise interrupted
    return bytes(buffers[0]), bytes(buffers[1]), outcome


def probe_version(provider: Provider, executable: str, timeout_seconds: float = 5.0,
                  output_limit: int = 65_536) -> ProbeResult:
    if not isinstance(provider, Provider):
        raise ValidationError("probe provider must be a Provider enum")
    ProviderConfig(executable, True)
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
        raise ValidationError("probe timeout must be finite and in (0, 30] seconds")
    bounded_int(output_limit, "output_limit", 1, 1_048_576)
    resolved = shutil.which(executable)
    if resolved is None:
        return ProbeResult(provider, "missing", None, detail="executable not found on PATH")
    try:
        process = subprocess.Popen([resolved, "--version"], shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=os.name == "posix")
    except FileNotFoundError:
        return ProbeResult(provider, "missing", resolved, detail="executable disappeared before launch")
    except OSError as exc:
        return ProbeResult(provider, "nonzero", resolved, detail=f"version probe could not execute: {exc}")
    stdout, stderr, outcome = _capture(process, float(timeout_seconds), output_limit)
    out, err = stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
    if outcome:
        return ProbeResult(provider, "timeout" if outcome == "timeout" else "invalid_output", resolved,
                           returncode=process.returncode, stdout=out, stderr=err, detail=outcome)
    if process.returncode != 0:
        return ProbeResult(provider, "nonzero", resolved, returncode=process.returncode, stdout=out, stderr=err,
                           detail="--version returned a nonzero exit status")
    version = re.search(r"(?<![A-Za-z0-9])v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)(?![A-Za-z0-9])", out + "\n" + err)
    if version is None:
        return ProbeResult(provider, "invalid_output", resolved, returncode=0, stdout=out, stderr=err,
                           detail="--version did not contain a recognizable version")
    candidate = version.group(1)
    try:
        text(candidate, "diagnostic version", 4096)
    except ValidationError:
        return ProbeResult(provider, "invalid_output", resolved, returncode=0, stdout=out, stderr=err,
                           detail="--version candidate exceeds supported format/length")
    return ProbeResult(provider, "available", resolved, candidate, 0, out, err)


def doctor(config: DiamondConfig) -> tuple[ProbeResult, ...]:
    return tuple(probe_version(provider, config.providers[provider].executable)
                 if config.providers[provider].enabled else ProbeResult(provider, "disabled", None,
                     detail="disabled by configuration; no process invoked") for provider in Provider)
