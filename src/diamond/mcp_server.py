"""Small stdio MCP bridge over the validated Diamond controller."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from .cli import main as controller_main
from .errors import DiamondError, ValidationError
from .paths import ProjectPaths, resolve_paths
from .validation import MAX_JSON_BYTES, canonical_json, command_id, task_id, text

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2024-11-05", PROTOCOL_VERSION})
MAX_MESSAGE_BYTES = MAX_JSON_BYTES


class McpRequestError(ValueError):
    """An invalid MCP request or tool argument."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
MUTATING_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}


def _schema(properties: dict[str, object], required: list[str] | None = None) -> dict[str, object]:
    value: dict[str, object] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        value["required"] = required
    return value


TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": "diamond_validate_config",
        "title": "Validate Diamond configuration",
        "description": "Validate the configured project without launching a provider or writing workflow state.",
        "inputSchema": _schema({}),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "diamond_project_status",
        "title": "Read Diamond project status",
        "description": "Read provider version probes and persisted workflow and delivery projections.",
        "inputSchema": _schema({}),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "diamond_list_workflows",
        "title": "List Diamond workflows",
        "description": "List persisted Builder/Debugger workflows without dispatching work.",
        "inputSchema": _schema({}),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "diamond_get_workflow",
        "title": "Inspect a Diamond workflow",
        "description": "Read one workflow projection by its immutable workflow ID.",
        "inputSchema": _schema({"workflow_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}}, ["workflow_id"]),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "diamond_get_workflow_events",
        "title": "Read workflow events",
        "description": "Read the immutable event history for one workflow.",
        "inputSchema": _schema({"workflow_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}}, ["workflow_id"]),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "diamond_list_deliveries",
        "title": "List candidate deliveries",
        "description": "List persisted candidate integration, QA, review, and release projections.",
        "inputSchema": _schema({}),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "diamond_get_delivery",
        "title": "Inspect a candidate delivery",
        "description": "Read one candidate delivery projection by its immutable delivery ID.",
        "inputSchema": _schema({"delivery_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}}, ["delivery_id"]),
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "diamond_create_workflow",
        "title": "Create a bounded workflow",
        "description": "Create a validated workflow record. This does not run a provider until a separate confirmed step is called.",
        "inputSchema": _schema({
            "objective": {"type": "string", "minLength": 1, "maxLength": 16384},
            "workspace": {"type": "string", "minLength": 1, "maxLength": 4096},
            "owned_paths": {"type": "array", "items": {"type": "string", "maxLength": 4096}, "minItems": 1},
            "tests": {"type": "array", "items": {"type": "object"}, "minItems": 1},
            "command_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"},
            "confirm": {"const": True, "description": "Explicit user approval for creating controller state."},
        }, ["objective", "workspace", "owned_paths", "tests", "command_id", "confirm"]),
        "annotations": MUTATING_ANNOTATIONS,
    },
    {
        "name": "diamond_run_workflow_step",
        "title": "Run one workflow step",
        "description": "Run exactly one explicitly confirmed Builder or Debugger step through the controller gates.",
        "inputSchema": _schema({
            "workflow_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
            "expected_revision": {"type": "integer", "minimum": 1},
            "confirm": {"const": True, "description": "Explicit user approval for provider execution."},
        }, ["workflow_id", "expected_revision", "confirm"]),
        "annotations": MUTATING_ANNOTATIONS,
    },
    {
        "name": "diamond_reconcile_workflow",
        "title": "Reconcile a blocked workflow",
        "description": "Record an explicit recovery decision for an interrupted workflow without blindly relaunching a provider.",
        "inputSchema": _schema({
            "workflow_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
            "expected_revision": {"type": "integer", "minimum": 1},
            "reason": {"type": "string", "minLength": 1, "maxLength": 16384},
            "confirm": {"const": True, "description": "Explicit user approval for recording reconciliation."},
        }, ["workflow_id", "expected_revision", "reason", "confirm"]),
        "annotations": MUTATING_ANNOTATIONS,
    },
)

_TOOL_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise McpRequestError(f"{name} must be an object")
    return value


def _no_arguments(arguments: object) -> dict[str, Any]:
    value = {} if arguments is None else _object(arguments, "arguments")
    if value:
        raise McpRequestError("this tool does not accept arguments")
    return value


def _required_text(arguments: dict[str, Any], name: str, maximum: int = 16_384) -> str:
    try:
        value = arguments[name]
    except KeyError as exc:
        raise McpRequestError(f"missing required argument: {name}") from exc
    try:
        return text(value, name, maximum)
    except DiamondError as exc:
        raise McpRequestError(str(exc)) from exc


def _workflow_id(arguments: dict[str, Any], name: str = "workflow_id") -> str:
    try:
        value = arguments[name]
        task_id(value)
        return value
    except (KeyError, DiamondError) as exc:
        raise McpRequestError(f"{name} must be 32 lowercase hexadecimal characters") from exc


def _confirm(arguments: dict[str, Any]) -> None:
    if arguments.get("confirm") is not True:
        raise McpRequestError("explicit confirm=true is required for this operation")


def _controller_args(paths: ProjectPaths, command: list[str]) -> list[str]:
    return ["--project", str(paths.project), "--config", str(paths.config), "--state-dir", str(paths.state), "--json", *command]


class McpServer:
    """A single-session MCP server that delegates to the existing controller."""

    def __init__(self, paths: ProjectPaths, *, invoke: Callable[[list[str]], tuple[int, dict[str, Any]]] | None = None) -> None:
        self.paths = paths
        self.initialized = False
        self._initialize_received = False
        self._invoke = invoke or self._invoke_controller

    def _invoke_controller(self, command: list[str]) -> tuple[int, dict[str, Any]]:
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = controller_main(_controller_args(self.paths, command))
        except BaseException as exc:
            print(f"controller invocation failed: {type(exc).__name__}", file=sys.stderr)
            return 1, {"schema_version": 1, "ok": False, "error": {"code": "INTERNAL_ERROR", "message": "controller invocation failed"}}
        raw = stdout.getvalue().strip()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = {"schema_version": 1, "ok": False, "error": {"code": "INTERNAL_ERROR", "message": stderr.getvalue().strip() or "controller returned invalid JSON"}}
        return code, payload

    @staticmethod
    def _response(identifier: object, result: object) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": identifier, "result": result}

    @staticmethod
    def _error(identifier: object, code: int, message: str, data: object | None = None) -> dict[str, object]:
        error: dict[str, object] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": identifier, "error": error}

    def handle(self, request: object) -> dict[str, object] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return self._error(request.get("id") if isinstance(request, dict) else None, -32600, "Invalid Request")
        identifier = request.get("id")
        if "id" in request and type(identifier) not in (str, int):
            return self._error(None, -32600, "Invalid Request ID")
        method = request["method"]
        is_notification = "id" not in request

        if method == "initialize":
            try:
                params = _object(request.get("params", {}), "initialize params")
            except McpRequestError as exc:
                return None if is_notification else self._error(identifier, -32602, str(exc))
            requested = params.get("protocolVersion")
            if not isinstance(requested, str) or not requested:
                return None if is_notification else self._error(identifier, -32602, "protocolVersion must be a non-empty string")
            selected = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
            if not is_notification:
                self._initialize_received = True
            return self._response(identifier, {
                "protocolVersion": selected,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "flawless-diamond-piece", "title": "Flawless Diamond Orchestrator", "version": "0.1.0"},
                "instructions": "Use read-only tools for inspection. Mutating tools require explicit confirm=true and remain subject to Diamond controller gates.",
            }) if not is_notification else None
        if method == "notifications/initialized":
            if is_notification and self._initialize_received:
                self.initialized = True
            return None
        if method == "ping":
            return None if is_notification else self._response(identifier, {})
        if not self.initialized:
            return None if is_notification else self._error(identifier, -32002, "Server is not initialized")
        if method == "tools/list":
            try:
                params = _object(request.get("params", {}), "tools/list params")
            except McpRequestError as exc:
                return None if is_notification else self._error(identifier, -32602, str(exc))
            if set(params) - {"cursor"}:
                return None if is_notification else self._error(identifier, -32602, "tools/list accepts only cursor")
            return None if is_notification else self._response(identifier, {"tools": list(TOOLS)})
        if method == "tools/call":
            if is_notification:
                return None
            try:
                result = self._call_tool(request.get("params"))
            except McpRequestError as exc:
                return self._error(identifier, -32602, str(exc))
            except Exception:
                return self._response(identifier, {"content": [{"type": "text", "text": "Diamond MCP tool failed."}], "isError": True})
            return self._response(identifier, result)
        return None if is_notification else self._error(identifier, -32601, f"Method not found: {method}")

    def _call_tool(self, params: object) -> dict[str, object]:
        value = _object(params, "tools/call params")
        name = value.get("name")
        if not isinstance(name, str) or name not in _TOOL_BY_NAME:
            raise McpRequestError(f"unknown tool: {name}")
        arguments = value.get("arguments", {})
        args = _object(arguments, "tool arguments")
        allowed = _TOOL_BY_NAME[name]["inputSchema"]["properties"]
        if set(args) - set(allowed):
            raise McpRequestError("unknown tool arguments: " + ", ".join(sorted(set(args) - set(allowed))))
        command: list[str]
        if name == "diamond_validate_config":
            _no_arguments(args)
            command = ["validate-config"]
        elif name == "diamond_project_status":
            _no_arguments(args)
            command = ["project", "status"]
        elif name == "diamond_list_workflows":
            _no_arguments(args)
            command = ["workflow", "list"]
        elif name == "diamond_get_workflow":
            command = ["workflow", "show", _workflow_id(args)]
        elif name == "diamond_get_workflow_events":
            command = ["workflow", "events", _workflow_id(args)]
        elif name == "diamond_list_deliveries":
            _no_arguments(args)
            command = ["delivery", "list"]
        elif name == "diamond_get_delivery":
            command = ["delivery", "show", _workflow_id(args, "delivery_id")]
        elif name == "diamond_create_workflow":
            _confirm(args)
            objective = _required_text(args, "objective")
            workspace = _required_text(args, "workspace", 4096)
            owned_paths = args.get("owned_paths")
            tests = args.get("tests")
            if not isinstance(owned_paths, list) or not owned_paths or not all(isinstance(path, str) for path in owned_paths):
                raise McpRequestError("owned_paths must be a non-empty list of strings")
            if not isinstance(tests, list) or not tests or not all(isinstance(test, dict) for test in tests):
                raise McpRequestError("tests must be a non-empty list of objects")
            identifier = _required_text(args, "command_id", 128)
            try:
                command_id(identifier)
                encoded_tests = [canonical_json(test) for test in tests]
            except DiamondError as exc:
                raise McpRequestError(str(exc)) from exc
            command = ["workflow", "create", "--objective", objective, "--workspace", workspace]
            for path in owned_paths:
                command.extend(("--owned-path", path))
            for test in encoded_tests:
                command.extend(("--test", test))
            command.extend(("--command-id", identifier))
        elif name == "diamond_run_workflow_step":
            _confirm(args)
            workflow_id = _workflow_id(args)
            revision = args.get("expected_revision")
            if type(revision) is not int or revision < 1:
                raise McpRequestError("expected_revision must be a positive integer")
            command = ["workflow", "run", workflow_id, "--expected-revision", str(revision)]
        elif name == "diamond_reconcile_workflow":
            _confirm(args)
            workflow_id = _workflow_id(args)
            revision = args.get("expected_revision")
            if type(revision) is not int or revision < 1:
                raise McpRequestError("expected_revision must be a positive integer")
            reason = _required_text(args, "reason")
            command = ["workflow", "reconcile", workflow_id, "--expected-revision", str(revision), "--reason", reason]
        else:
            raise McpRequestError(f"unknown tool: {name}")
        code, payload = self._invoke(command)
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        result: dict[str, object] = {"content": [{"type": "text", "text": serialized}], "isError": code != 0}
        if isinstance(payload, dict):
            result["structuredContent"] = payload
        return result


def serve_stdio(paths: ProjectPaths, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Run newline-delimited JSON-RPC over stdio until the client closes stdin."""
    server = McpServer(paths)
    stream = stdin.buffer if hasattr(stdin, "buffer") else stdin
    while True:
        raw_line = stream.readline(MAX_MESSAGE_BYTES + 1)
        if not raw_line:
            break
        if len(raw_line) > MAX_MESSAGE_BYTES:
            ending = b"\n" if isinstance(raw_line, bytes) else "\n"
            remainder = raw_line
            while remainder and not remainder.endswith(ending):
                remainder = stream.readline(MAX_MESSAGE_BYTES + 1)
            response = server._error(None, -32700, "Message exceeds the 1 MiB limit")
        elif isinstance(raw_line, bytes):
            if len(raw_line) > MAX_MESSAGE_BYTES:
                response = server._error(None, -32700, "Message exceeds the 1 MiB limit")
            else:
                try:
                    request = json.loads(raw_line.decode("utf-8"))
                    response = server.handle(request)
                except (UnicodeDecodeError, ValueError, RecursionError):
                    response = server._error(None, -32700, "Parse error")
        else:
            if len(raw_line.encode("utf-8")) > MAX_MESSAGE_BYTES:
                response = server._error(None, -32700, "Message exceeds the 1 MiB limit")
            else:
                try:
                    response = server.handle(json.loads(raw_line))
                except (UnicodeDecodeError, ValueError, RecursionError):
                    response = server._error(None, -32700, "Parse error")
        if response is not None:
            stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n")
            stdout.flush()
    return 0


def run_from_cli(project: str, config: str | None, state_dir: str | None, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    paths = resolve_paths(project, config, state_dir)
    return serve_stdio(paths, stdin, stdout)
