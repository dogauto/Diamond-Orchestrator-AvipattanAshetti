import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .adapters import Invocation, OutputMode
from .errors import ValidationError
from .supervisor import ExecutionResult, ExecutionStatus
from .validation import canonical_json, text


class ParseStatus(str, Enum):
    VALID = "valid"
    EMPTY = "empty"
    MALFORMED = "malformed"
    EXECUTION_FAILED = "execution_failed"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class NormalizedResult:
    status: ParseStatus
    content: str | None
    reported_model: str | None
    session_id: str | None
    payloads: tuple[str, ...]
    detail: str = ""
    structured_output: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ParseStatus) or type(self.payloads) is not tuple:
            raise ValidationError("normalized result fields are invalid")
        for value, name in ((self.content, "result content"),
                            (self.reported_model, "reported model"),
                            (self.session_id, "session id")):
            if value is not None:
                text(value, name, 1_000_000 if name == "result content" else 4096)
        if type(self.detail) is not str or len(self.detail) > 4096:
            raise ValidationError("result detail must be text of at most 4096 characters")
        if self.structured_output is not None:
            text(self.structured_output, "structured output", 1_000_000)
        for payload in self.payloads:
            text(payload, "result payload", 1_000_000)


def _nested_string_field(payload: Any, keys: tuple[str, ...], depth: int = 0) -> str | None:
    if depth > 8:
        return None
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            found = _nested_string_field(value, keys, depth + 1)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _nested_string_field(value, keys, depth + 1)
            if found:
                return found
    return None


def _content_field(payload: dict[str, Any]) -> str | None:
    event_type = payload.get("type")
    if event_type == "assistant.message":
        data = payload.get("data")
        if isinstance(data, dict):
            value = data.get("content")
            if isinstance(value, str) and value:
                return value
        return None
    if event_type in {"user.message", "tool.execution_complete", "tool.execution_partial_result"}:
        return None
    for key in ("result", "output", "text", "content", "response", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return _nested_string_field(payload, ("result", "output", "text", "content", "response"))


def _reported_model(payloads: tuple[dict[str, Any], ...]) -> str | None:
    direct = next((_nested_string_field(item, ("model", "model_name")) for item in reversed(payloads)
                   if _nested_string_field(item, ("model", "model_name"))), None)
    if direct:
        return direct
    for payload in reversed(payloads):
        stats = payload.get("stats")
        models = stats.get("models") if isinstance(stats, dict) else None
        if not isinstance(models, dict):
            continue
        successful: list[str] = []
        names: list[str] = []
        for name, value in models.items():
            if not isinstance(name, str):
                continue
            names.append(name)
            api = value.get("api") if isinstance(value, dict) else None
            if isinstance(api, dict) and api.get("totalErrors") == 0:
                successful.append(name)
        if successful:
            return successful[-1]
        if names:
            return names[-1]
    return None


def _provider_error(payloads: tuple[dict[str, Any], ...]) -> str | None:
    for payload in reversed(payloads):
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        status = result.get("status") if isinstance(result, dict) else None
        if status in {"ERROR", "CANCELED", "INTERRUPTED", "INVALID"}:
            error = result.get("error") or result.get("message") or status
            return str(error)
    return None


def _structured_output(payloads: tuple[dict[str, Any], ...]) -> str | None:
    for payload in reversed(payloads):
        value: Any = payload.get("structured_output")
        if value is None and isinstance(payload.get("result"), dict):
            value = payload["result"].get("structured_output")
        if isinstance(value, dict):
            return canonical_json(value)
    return None


def _event_content(payloads: tuple[dict[str, Any], ...]) -> str | None:
    assistant_messages: list[tuple[str | None, int | None, str]] = []
    for payload in payloads:
        if payload.get("type") != "assistant.message":
            continue
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            continue
        message_id = data.get("messageId") if isinstance(data.get("messageId"), str) else None
        chunk_index = data.get("chunkIndex") if type(data.get("chunkIndex")) is int else None
        assistant_messages.append((message_id, chunk_index, data["content"]))
    if assistant_messages:
        message_id = assistant_messages[-1][0]
        selected = [item for item in assistant_messages if item[0] == message_id]
        if all(item[1] is not None for item in selected):
            selected.sort(key=lambda item: item[1])  # type: ignore[arg-type]
        return "".join(item[2] for item in selected)
    for payload in reversed(payloads):
        content = _content_field(payload)
        if content:
            return content
    return None


def _json_payloads(stdout: str) -> tuple[dict[str, Any], ...] | None:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return tuple()
    try:
        value = json.loads(stdout)
        return (value,) if isinstance(value, dict) else None
    except json.JSONDecodeError:
        payloads: list[dict[str, Any]] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                return None
            if not isinstance(value, dict):
                return None
            payloads.append(value)
        return tuple(payloads)


def parse_result(invocation: Invocation, execution: ExecutionResult) -> NormalizedResult:
    if execution.status is not ExecutionStatus.SUCCEEDED:
        return NormalizedResult(ParseStatus.EXECUTION_FAILED, None, None, None, tuple(), execution.detail)
    if invocation.output_mode is OutputMode.TEXT:
        content = execution.stdout.strip()
        if not content:
            return NormalizedResult(ParseStatus.EMPTY, None, None, None, tuple(), "provider returned no text")
        return NormalizedResult(ParseStatus.VALID, content, None, None, (canonical_json({"text": content}),))
    payloads = _json_payloads(execution.stdout)
    if payloads is None:
        return NormalizedResult(ParseStatus.MALFORMED, None, None, None, tuple(), "provider returned malformed JSON")
    if not payloads:
        return NormalizedResult(ParseStatus.EMPTY, None, None, None, tuple(), "provider returned no JSON")
    model = _reported_model(payloads)
    session = next((_nested_string_field(item, ("session_id", "sessionId", "conversation_id", "thread_id"))
                    for item in reversed(payloads)
                    if _nested_string_field(item, ("session_id", "sessionId", "conversation_id", "thread_id"))), None)
    content = _event_content(payloads)
    serialized = tuple(canonical_json(item) for item in payloads)
    provider_error = _provider_error(payloads)
    if provider_error:
        return NormalizedResult(ParseStatus.PROVIDER_ERROR, None, model, session,
                                 serialized, provider_error)
    structured_output = _structured_output(payloads)
    status = ParseStatus.VALID if content or structured_output else ParseStatus.EMPTY
    detail = "" if content else "provider returned JSON without assistant content"
    return NormalizedResult(status, content, model, session, serialized, detail, structured_output)
