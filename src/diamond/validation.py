import json
import math
import re
from datetime import datetime, timezone
from typing import Mapping

from .errors import ValidationError

MAX_JSON_BYTES = 1_048_576


def bounded_int(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def text(value: object, name: str, maximum: int = 16_384) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValidationError(f"{name} must be nonblank text of at most {maximum} characters")
    if "\x00" in value:
        raise ValidationError(f"{name} must not contain NUL")
    return value


def task_id(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValidationError("task ID must be 32 lowercase hexadecimal UUID characters")
    return value


def command_id(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None:
        raise ValidationError("command ID must be 1..128 letters/digits or . _ : -, starting with a letter/digit")
    return value


def exact_keys(value: object, keys: set[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValidationError(f"{name} requires exactly these fields: {', '.join(sorted(keys))}")
    return value


def canonical_json(value: object) -> str:
    def check(item: object, depth: int) -> None:
        if depth > 32:
            raise ValidationError("JSON nesting exceeds 32 levels")
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ValidationError("payload must contain strict JSON values with string object keys")

    check(value, 0)
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(result.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValidationError("JSON payload exceeds 1 MiB")
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("payload cannot be serialized as strict JSON") from exc


def parse_canonical_json(value: object) -> object:
    if not isinstance(value, str) or len(value) > MAX_JSON_BYTES:
        raise ValidationError("payload_json must be canonical JSON text of at most 1 MiB")
    try:
        result = json.loads(value)
        if canonical_json(result) != value:
            raise ValidationError("payload_json is not canonical JSON")
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("payload_json is not strict canonical JSON") from exc


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_timestamp(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value) is None:
        raise ValidationError("timestamp must be UTC RFC3339 with microseconds and Z")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("timestamp is not a valid date/time") from exc
    return value
