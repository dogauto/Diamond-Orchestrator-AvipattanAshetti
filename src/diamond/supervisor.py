import asyncio
import hashlib
import math
import os
import shutil
import signal
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from .adapters import Invocation, validate_working_directory
from .errors import ValidationError
from .processes import ProcessIdentity, identify
from .sandbox import prepare_read_only
from .validation import bounded_int


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    NONZERO = "nonzero"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    START_FAILED = "start_failed"
    CANCELLED = "cancelled"
    CLEANUP_FAILED = "cleanup_failed"


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    returncode: int | None
    stdout: str
    stderr: str
    detail: str = ""
    stdout_bytes: int | None = None
    stderr_bytes: int | None = None
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None
    cleanup_complete: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionStatus):
            raise ValidationError("execution status is invalid")
        if self.returncode is not None and type(self.returncode) is not int:
            raise ValidationError("execution return code must be an integer or None")
        if not all(type(value) is str for value in (self.stdout, self.stderr, self.detail)):
            raise ValidationError("execution output must be text")
        for value in (self.stdout_bytes, self.stderr_bytes):
            if value is not None:
                bounded_int(value, "captured bytes", 0, 16_777_216)
        if type(self.cleanup_complete) is not bool:
            raise ValidationError("cleanup_complete must be boolean")


class CancelledExecution(asyncio.CancelledError):
    def __init__(self, result: ExecutionResult) -> None:
        super().__init__("execution cancelled after bounded cleanup")
        self.result = result


async def _read_stream(stream: asyncio.StreamReader, limit: int,
                       usage: list[int], lock: asyncio.Lock, data: bytearray) -> tuple[bytes, bool]:
    overflow = False
    while True:
        chunk = await stream.read(min(8192, limit + 1))
        if not chunk:
            break
        async with lock:
            capacity = limit - usage[0]
            keep = max(0, min(len(chunk), capacity))
            if keep:
                data.extend(chunk[:keep])
                usage[0] += keep
            if len(chunk) > capacity:
                overflow = True
                break
    return bytes(data), overflow


async def _terminate(process: asyncio.subprocess.Process) -> bool:
    loop = asyncio.get_running_loop()
    try:
        for sig, grace in ((signal.SIGTERM, 0.25), (signal.SIGKILL, 3.0)):
            try:
                if os.name == "posix":
                    os.killpg(process.pid, sig)
                elif process.returncode is None:
                    process.terminate() if sig == signal.SIGTERM else process.kill()
            except ProcessLookupError:
                pass
            deadline = loop.time() + grace
            while loop.time() < deadline:
                group_alive = False
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, 0)
                        group_alive = True
                    except ProcessLookupError:
                        pass
                if process.returncode is not None and not group_alive:
                    await asyncio.wait_for(process.wait(), timeout=max(0.001, deadline - loop.time()))
                    return True
                await asyncio.sleep(0.01)
        return False
    except (asyncio.TimeoutError, OSError):
        return False


async def _release(process: asyncio.subprocess.Process, readers: list[asyncio.Task]) -> bool:
    clean = True
    transport = getattr(process, "_transport", None)
    try:
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        # Process.wait() may wait for pipe EOF even after the child has exited.
        _close_pipes(process)
        clean = await _terminate(process)
    except OSError:
        clean = False
    finally:
        if transport is not None:
            transport.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except (asyncio.TimeoutError, OSError):
            clean = False
        await asyncio.sleep(0)
    return clean


def _close_pipes(process: asyncio.subprocess.Process) -> None:
    """Close asyncio subprocess pipe transports after the streams are drained."""
    transport = getattr(process, "_transport", None)
    if transport is None:
        return
    for descriptor in (1, 2):
        pipe = transport.get_pipe_transport(descriptor)
        if pipe is not None:
            pipe.close()


async def run(invocation: Invocation, cwd: Path, timeout_seconds: float = 900,
               output_limit: int = 1_048_576,
               environment: dict[str, str] | None = None, *,
               on_started: Callable[[int, ProcessIdentity | None], None] | None = None,
               enforce_paths: bool = False, owned_paths: tuple[str, ...] = ()) -> ExecutionResult:
    """Run one explicitly constructed invocation with bounded resources."""
    cwd = validate_working_directory(cwd)
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 86_400:
        raise ValidationError("execution timeout must be finite and within 0..86400 seconds")
    bounded_int(output_limit, "execution output_limit", 1, 16_777_216)
    env = os.environ.copy()
    if environment:
        env.update(environment)
    command = invocation.argv
    sandbox_runtime: Path | None = None
    if enforce_paths or invocation.permission_mode.value == "plan" and invocation.provider.value == "omnirush":
        try:
            command, sandbox_runtime = prepare_read_only(cwd, invocation.argv, env, invocation.provider.value,
                                                        owned_paths if enforce_paths else ())
        except ValidationError as exc:
            return ExecutionResult(ExecutionStatus.START_FAILED, None, "", "", str(exc))
    try:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix")
    except (OSError, ValueError) as exc:
        if sandbox_runtime is not None:
            shutil.rmtree(sandbox_runtime, ignore_errors=True)
        return ExecutionResult(ExecutionStatus.START_FAILED, None, "", "", str(exc))
    assert process.stdout is not None and process.stderr is not None
    usage = [0]
    output_lock = asyncio.Lock()
    buffers = [bytearray(), bytearray()]
    readers = [asyncio.create_task(_read_stream(process.stdout, output_limit, usage, output_lock, buffers[0])),
               asyncio.create_task(_read_stream(process.stderr, output_limit, usage, output_lock, buffers[1]))]
    outcome: ExecutionStatus | None = None
    detail = ""
    pending = set(readers)
    try:
        if on_started is not None:
            try:
                identity = identify(process.pid)
            except (OSError, ValidationError):
                identity = None
            on_started(process.pid, identity)
        deadline = asyncio.get_running_loop().time() + float(timeout_seconds)
        while pending and outcome is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                outcome, detail = ExecutionStatus.TIMEOUT, "deadline exceeded"
                break
            done, pending = await asyncio.wait(pending, timeout=remaining,
                                               return_when=asyncio.FIRST_COMPLETED)
            if not done:
                outcome, detail = ExecutionStatus.TIMEOUT, "deadline exceeded"
                break
            for task in done:
                if task.result()[1]:
                    outcome, detail = ExecutionStatus.OUTPUT_LIMIT, "output limit exceeded"
                    break
        if outcome is None:
            remaining = max(0.001, deadline - asyncio.get_running_loop().time())
            try:
                await asyncio.wait_for(process.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                outcome, detail = ExecutionStatus.TIMEOUT, "deadline exceeded"
    except asyncio.CancelledError:
        outcome, detail = ExecutionStatus.CANCELLED, "execution cancelled"
    except Exception as exc:
        outcome, detail = ExecutionStatus.START_FAILED, f"capture failed: {exc}"

    cancelled = outcome is ExecutionStatus.CANCELLED
    cleanup = asyncio.create_task(_release(process, readers))
    while True:
        try:
            cleanup_failed = not await asyncio.shield(cleanup)
            break
        except asyncio.CancelledError:
            cancelled = True
            outcome, detail = ExecutionStatus.CANCELLED, "execution cancelled"
    if cleanup_failed:
        outcome, detail = ExecutionStatus.CLEANUP_FAILED, "process group cleanup could not be proven"
    stdout, stderr = bytes(buffers[0]), bytes(buffers[1])
    try:
        status = outcome or (ExecutionStatus.SUCCEEDED if process.returncode == 0 else ExecutionStatus.NONZERO)
        result = ExecutionResult(status, process.returncode, stdout.decode("utf-8", "replace"),
                                 stderr.decode("utf-8", "replace"), detail, len(stdout), len(stderr),
                                 hashlib.sha256(stdout).hexdigest(), hashlib.sha256(stderr).hexdigest(), not cleanup_failed)
        if cancelled:
            raise CancelledExecution(result)
        return result
    finally:
        if sandbox_runtime is not None:
            shutil.rmtree(sandbox_runtime, ignore_errors=True)
