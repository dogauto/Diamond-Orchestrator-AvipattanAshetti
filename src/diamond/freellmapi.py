"""Opt-in gateway diagnostics; not a workflow provider or release authority."""

import json
import math
import os
import re
import stat
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .errors import ProviderAuthError, ProviderError, StorageError, ValidationError
from .gateway_http import request_json
from .validation import text

MAX_CONFIG_BYTES = 16_384
MAX_MODELS = 256
MAX_PROMPT_LENGTH = 16_384
API_KEY_ENV_PATTERN = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}\Z")
SMOKE_PROMPT = "Respond with the exact word OK."


def validate_model(value: str) -> str:
    if not isinstance(value, str) or MODEL_PATTERN.fullmatch(value) is None:
        raise ValidationError("FreeLLMAPI model must be a printable model ID of at most 256 characters")
    return value


def explicit_model(value: str) -> str:
    validate_model(value)
    if value.lower() in {"auto", "smartest", "default"}:
        raise ValidationError("select an explicit FreeLLMAPI model, not an automatic routing alias")
    return value


def normalize_base_url(value: str) -> str:
    text(value, "FreeLLMAPI base URL", 2048)
    # Validate before urlsplit, which silently strips tabs/newlines and leading controls.
    if any(ord(char) <= 32 or ord(char) >= 127 for char in value) or any(char in value for char in "\\%?#@"):
        raise ValidationError("FreeLLMAPI URL must not contain credentials, controls, escapes, query or fragment")
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ValidationError("invalid FreeLLMAPI base URL") from None
    if parts.scheme not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        raise ValidationError("FreeLLMAPI URL must be HTTP(S) without credentials")
    if port is not None and not 1 <= port <= 65535:
        raise ValidationError("FreeLLMAPI URL port must be in 1..65535")
    if host.startswith("[") or host.endswith("]"):
        host_for_validation = host[1:-1]
    else:
        host_for_validation = host
    if re.fullmatch(r"[A-Za-z0-9.:-]+", host_for_validation) is None:
        raise ValidationError("invalid FreeLLMAPI hostname")
    if parts.scheme == "http" and host.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValidationError("unencrypted FreeLLMAPI HTTP is restricted to loopback addresses")
    # Both the gateway root and OpenAI API root are accepted; arbitrary credential-bearing paths are not.
    path = parts.path.rstrip("/")
    if path not in {"", "/v1"}:
        raise ValidationError("FreeLLMAPI URL must use the gateway root or /v1")
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, "", ""))


@dataclass(frozen=True)
class FreeLLMAPIConfig:
    base_url: str
    api_key_env: str = "FREELLMAPI_API_KEY"
    default_model: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", normalize_base_url(self.base_url))
        if not isinstance(self.api_key_env, str) or API_KEY_ENV_PATTERN.fullmatch(self.api_key_env) is None:
            raise ValidationError("FreeLLMAPI api_key_env must be an uppercase environment variable name")
        if self.default_model is not None:
            explicit_model(self.default_model)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FreeLLMAPIProbe:
    base_url: str
    models: tuple[str, ...] = ()
    requested_model: str | None = None
    reported_model: str | None = None
    response_chars: int | None = None
    latency_ms: float | None = None
    usage: dict[str, int] | None = None
    marker_matched: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update(provider="freellmapi", models=list(self.models), auth_status="REQUEST_ACCEPTED",
                      workflow_execution="UNAVAILABLE", client_retries=0, fallback_status="NOT_REPORTED",
                      model_mismatch=(self.reported_model != self.requested_model
                                      if self.reported_model is not None and self.requested_model is not None else None))
        return result


def _check_config_path(path: Path) -> None:
    if ".." in path.parts:
        raise ValidationError("FreeLLMAPI config path must not contain traversal")
    for parent in path.parents:
        if parent.is_symlink() and not (
            str(parent) in {"/var", "/tmp"} and str(parent.resolve()) == "/private" + str(parent)
        ):
            raise ValidationError("FreeLLMAPI config parent must not be a symlink")
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValidationError("FreeLLMAPI config must be a regular, non-symlink file")


def load_gateway_config(path: Path) -> FreeLLMAPIConfig:
    _check_config_path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
                raise ValidationError("FreeLLMAPI config must be a regular file of at most 16 KiB")
            data = handle.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            raise ValidationError("FreeLLMAPI config exceeds 16 KiB")
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        raise ValidationError("FreeLLMAPI config must contain valid JSON") from None
    except OSError:
        raise StorageError("cannot read FreeLLMAPI configuration; check the path and permissions") from None
    if not isinstance(raw, dict) or set(raw) - {"base_url", "api_key_env", "default_model"} or "base_url" not in raw:
        raise ValidationError("FreeLLMAPI config requires base_url and only api_key_env/default_model as optional fields")
    return FreeLLMAPIConfig(**raw)


def reject_config_secret(config: FreeLLMAPIConfig, environment: Mapping[str, str]) -> None:
    key = environment.get(config.api_key_env)
    if key and any(key in value for value in (config.base_url, config.default_model or "", config.api_key_env)):
        raise ValidationError("credential material must not be used in gateway configuration")


def save_gateway_config(path: Path, config: FreeLLMAPIConfig) -> None:
    """Atomic, owner-only non-secret configuration; no credential value is accepted."""
    reject_config_secret(config, os.environ)
    _check_config_path(path)
    payload = (json.dumps(config.to_dict(), sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _check_config_path(path)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _check_config_path(path)
        os.replace(temporary, path)
    except OSError:
        raise StorageError("cannot save FreeLLMAPI configuration; check the path and permissions") from None
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


class FreeLLMAPIClient:
    """A single-response diagnostic client; no tools, fallback or automatic retry."""

    def __init__(self, config: FreeLLMAPIConfig, *, environment: Mapping[str, str] | None = None,
                 timeout_seconds: float = 10.0) -> None:
        if not isinstance(config, FreeLLMAPIConfig):
            raise ValidationError("FreeLLMAPI client requires validated configuration")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise ValidationError("FreeLLMAPI timeout must be finite and in (0, 30] seconds")
        self.config = config
        self.environment = os.environ if environment is None else environment
        self.timeout_seconds = float(timeout_seconds)
        self.last_latency_ms: float | None = None
        reject_config_secret(config, self.environment)

    @property
    def auth_status(self) -> str:
        return "PRESENT_NOT_CHECKED" if self.environment.get(self.config.api_key_env) else "MISSING"

    def _api_key(self) -> str:
        value = self.environment.get(self.config.api_key_env)
        if not value:
            raise ProviderAuthError(f"FreeLLMAPI credential is missing from ${self.config.api_key_env}")
        if not isinstance(value, str) or len(value) > 4096 or any(not 33 <= ord(char) <= 126 for char in value):
            raise ProviderAuthError("FreeLLMAPI credential must be nonblank printable ASCII without whitespace")
        return value

    def _request(self, resource: str, body: dict[str, Any] | None = None) -> Any:
        key = self._api_key()
        suffix = resource if self.config.base_url.endswith("/v1") else "/v1" + resource
        started = time.monotonic()
        try:
            return request_json(self.config.base_url + suffix, key, body, self.timeout_seconds)
        finally:
            self.last_latency_ms = round((time.monotonic() - started) * 1000, 3)

    def _reported_id(self, value: Any) -> str:
        try:
            validate_model(value)
        except ValidationError:
            raise ProviderError("FreeLLMAPI returned an invalid model ID") from None
        if self._api_key() in value:
            raise ProviderError("FreeLLMAPI returned credential material in a model ID")
        return value

    def list_models(self) -> tuple[str, ...]:
        payload = self._request("/models")
        entries = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(entries, list) or len(entries) > MAX_MODELS:
            raise ProviderError("FreeLLMAPI model response must contain data with at most 256 models")
        models = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ProviderError("FreeLLMAPI returned an invalid model entry")
            models.append(self._reported_id(entry.get("id")))
        return tuple(dict.fromkeys(models))

    def smoke(self, model: str, prompt: str = SMOKE_PROMPT) -> FreeLLMAPIProbe:
        explicit_model(model)
        text(prompt, "FreeLLMAPI smoke prompt", MAX_PROMPT_LENGTH)
        key = self._api_key()
        if key in model or key in prompt:
            raise ValidationError("credential material must not be used in a model or prompt")
        payload = self._request("/chat/completions", {
            "model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 64, "stream": False,
        })
        if not isinstance(payload, dict) or "error" in payload:
            raise ProviderError("FreeLLMAPI returned an error or invalid completion")
        reported = payload.get("model")
        if reported is not None:
            reported = self._reported_id(reported)
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProviderError("FreeLLMAPI smoke response must contain one choice")
        message = choices[0].get("message")
        if not isinstance(message, dict) or message.get("tool_calls") or message.get("function_call"):
            raise ProviderError("FreeLLMAPI smoke response must be text-only")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("FreeLLMAPI smoke response did not contain nonempty text")
        if choices[0].get("finish_reason") != "stop":
            raise ProviderError("FreeLLMAPI smoke response did not complete normally")
        marker = content.strip() == "OK" if prompt == SMOKE_PROMPT else None
        if marker is False:
            raise ProviderError("FreeLLMAPI smoke response did not match the requested OK marker")
        usage = payload.get("usage")
        if usage is not None:
            fields = {"prompt_tokens", "completion_tokens", "total_tokens"}
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or not 0 <= usage[k] < 2**63 for k in fields):
                raise ProviderError("FreeLLMAPI returned invalid token usage")
            usage = {k: usage[k] for k in sorted(fields)}
        return FreeLLMAPIProbe(self.config.base_url, requested_model=model, reported_model=reported,
                               response_chars=len(content), latency_ms=self.last_latency_ms, usage=usage,
                               marker_matched=marker)


def configured_probe(config: FreeLLMAPIConfig, client: FreeLLMAPIClient) -> FreeLLMAPIProbe:
    models = client.list_models()
    return FreeLLMAPIProbe(config.base_url, models=models, latency_ms=client.last_latency_ms)
