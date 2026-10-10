"""HTTP diagnostics isolated in a disposable process for a wall-clock deadline."""

import json
import multiprocessing
import urllib.error
import urllib.request
from http.client import HTTPException
from multiprocessing.connection import Connection
from typing import Any

from .errors import ProviderAuthError, ProviderError

MAX_RESPONSE_BYTES = 1_048_576


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def _fetch(url: str, key: str, body: dict[str, Any] | None, timeout: float) -> bytes:
    # FreeLLMAPI's local gateway authenticates API keys with x-api-key.
    # Do not substitute Authorization: Bearer; the gateway rejects that form.
    headers = {"Accept": "application/json", "X-API-Key": key,
               "User-Agent": "diamond-freellmapi/1", "Accept-Encoding": "identity"}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    # Ambient proxy configuration must not forward the operator's credential.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise ProviderError(f"FreeLLMAPI returned HTTP {response.status}")
            if response.headers.get_content_type() != "application/json":
                raise ProviderError("FreeLLMAPI did not return application/json")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ProviderError("FreeLLMAPI returned an unsupported content encoding")
            declared = response.headers.get("Content-Length")
            if declared is not None:
                if not declared.isdecimal() or len(declared) > 10:
                    raise ProviderError("FreeLLMAPI returned an invalid content length")
                if int(declared) > MAX_RESPONSE_BYTES:
                    raise ProviderError("FreeLLMAPI response exceeds the 1 MiB limit")
            result = response.read(MAX_RESPONSE_BYTES + 1)
            if len(result) > MAX_RESPONSE_BYTES:
                raise ProviderError("FreeLLMAPI response exceeds the 1 MiB limit")
            if declared is not None and len(result) != int(declared):
                raise ProviderError("FreeLLMAPI response was truncated")
            return result
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        if code in {401, 403}:
            raise ProviderAuthError("FreeLLMAPI rejected the configured credential") from None
        if code == 429:
            raise ProviderError("FreeLLMAPI rate limit reached; no retry was attempted") from None
        if 300 <= code < 400:
            raise ProviderError("FreeLLMAPI redirects are disabled") from None
        raise ProviderError(f"FreeLLMAPI returned HTTP {code}") from None
    except (urllib.error.URLError, HTTPException, OSError, ValueError):
        raise ProviderError("FreeLLMAPI connection failed, timed out, or was interrupted") from None


def _worker(pipe: Connection, url: str, key: str, body: dict[str, Any] | None, timeout: float) -> None:
    try:
        pipe.send_bytes(b"O" + _fetch(url, key, body, timeout))
    except ProviderAuthError as exc:
        pipe.send_bytes(b"A" + str(exc).encode("utf-8"))
    except ProviderError as exc:
        pipe.send_bytes(b"E" + str(exc).encode("utf-8"))
    except Exception:
        pipe.send_bytes(b"EFreeLLMAPI request failed")
    finally:
        pipe.close()


def request_json(url: str, key: str, body: dict[str, Any] | None, timeout: float) -> Any:
    """Bound DNS, TLS, response headers and slow-drip bodies, not just socket idle time."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, url, key, body, timeout), daemon=True)
    try:
        process.start()
        sender.close()
        if not receiver.poll(timeout):
            raise ProviderError("FreeLLMAPI request exceeded its wall-clock deadline")
        message = receiver.recv_bytes(MAX_RESPONSE_BYTES + 1)
    except (OSError, EOFError, ValueError):
        raise ProviderError("FreeLLMAPI HTTP worker failed") from None
    finally:
        receiver.close()
        sender.close()
        if process.pid is not None:
            process.join(0.05)
            if process.is_alive():
                process.terminate()
                process.join(0.2)
            if process.is_alive():
                process.kill()
                process.join(1)
            if process.is_alive():
                raise ProviderError("FreeLLMAPI HTTP worker cleanup failed")
        process.close()
    if message[:1] == b"A":
        raise ProviderAuthError(message[1:].decode("utf-8"))
    if message[:1] != b"O":
        raise ProviderError(message[1:].decode("utf-8"))
    try:
        return json.loads(message[1:].decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ProviderError("FreeLLMAPI returned invalid JSON") from None


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON")
