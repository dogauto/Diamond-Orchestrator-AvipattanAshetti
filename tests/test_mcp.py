import io
import json
import sys
from diamond.mcp_server import McpServer, serve_stdio
from diamond.paths import resolve_paths
from support import ProjectCase


class McpTests(ProjectCase):
    def make_server(self, invoke=None) -> McpServer:
        paths = resolve_paths(str(self.project), "config/diamond.toml", str(self.base / "state"))
        return McpServer(paths, invoke=invoke)

    def initialize(self, server: McpServer) -> None:
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        })
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_requires_initialize_and_exposes_only_declared_tools(self) -> None:
        server = self.make_server()
        before = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(before["error"]["code"], -32002)
        self.initialize(server)
        response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(names, {
            "diamond_validate_config", "diamond_project_status", "diamond_list_workflows",
            "diamond_get_workflow", "diamond_get_workflow_events", "diamond_list_deliveries",
            "diamond_get_delivery", "diamond_create_workflow", "diamond_run_workflow_step",
            "diamond_reconcile_workflow",
        })
        self.assertTrue(all("inputSchema" in tool for tool in response["result"]["tools"]))

    def test_read_only_tool_reuses_controller_and_does_not_create_state(self) -> None:
        server = self.make_server()
        self.initialize(server)
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "diamond_validate_config", "arguments": {}},
        })
        self.assertFalse(response["result"]["isError"])
        self.assertTrue(response["result"]["structuredContent"]["ok"])
        self.assertFalse((self.project / ".diamond").exists())

    def test_mutating_tools_require_explicit_confirmation_and_use_fixed_commands(self) -> None:
        calls = []

        def invoke(command):
            calls.append(command)
            return 0, {"schema_version": 1, "ok": True, "result": {"accepted": True}}

        server = self.make_server(invoke)
        self.initialize(server)
        rejected = server.handle({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "diamond_create_workflow", "arguments": {
                "objective": "Create a bounded task", "workspace": str(self.base),
                "owned_paths": ["owned.py"], "tests": [{"name": "check"}], "command_id": "mcp:create:1",
            }},
        })
        self.assertEqual(rejected["error"]["code"], -32602)
        self.assertEqual(calls, [])

        accepted = server.handle({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "diamond_create_workflow", "arguments": {
                "objective": "Create a bounded task", "workspace": str(self.base),
                "owned_paths": ["owned.py"], "tests": [{"name": "check"}],
                "command_id": "mcp:create:1", "confirm": True,
            }},
        })
        self.assertFalse(accepted["result"]["isError"])
        self.assertEqual(calls[0][0:3], ["workflow", "create", "--objective"])
        self.assertNotIn("--shell", calls[0])

    def test_real_create_tool_persists_only_through_the_controller(self) -> None:
        workspace = self.base / "workflow workspace"
        workspace.mkdir()
        (workspace / "owned.py").write_text("value = 1\n", encoding="utf-8")
        (workspace / "check.py").write_text("from pathlib import Path\nassert Path('owned.py').is_file()\n", encoding="utf-8")
        server = self.make_server()
        self.initialize(server)
        response = server.handle({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "diamond_create_workflow", "arguments": {
                "objective": "Record a bounded MCP workflow", "workspace": str(workspace),
                "owned_paths": ["owned.py"],
                "tests": [{"name": "workspace check", "argv": [sys.executable, "check.py"], "timeout_seconds": 5, "output_limit": 4096}],
                "command_id": "mcp:real-create:1", "confirm": True,
            }},
        })
        self.assertFalse(response["result"]["isError"])
        workflow = response["result"]["structuredContent"]["result"]["workflow"]
        self.assertEqual(workflow["status"], "CREATED")
        self.assertTrue((self.base / "state" / "diamond.sqlite3").is_file())

    def test_invalid_tool_arguments_are_protocol_errors_and_unknown_tools_are_rejected(self) -> None:
        server = self.make_server()
        self.initialize(server)
        invalid = server.handle({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "diamond_get_workflow", "arguments": {"workflow_id": "../escape"}},
        })
        self.assertEqual(invalid["error"]["code"], -32602)
        unknown = server.handle({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "run_arbitrary_command", "arguments": {}},
        })
        self.assertEqual(unknown["error"]["code"], -32602)

    def test_malformed_lifecycle_parameters_return_protocol_errors(self) -> None:
        server = self.make_server()
        initialize = server.handle({"jsonrpc": "2.0", "id": 8, "method": "initialize", "params": []})
        self.assertEqual(initialize["error"]["code"], -32602)
        self.initialize(server)
        listing = server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": []})
        self.assertEqual(listing["error"]["code"], -32602)

    def test_stdio_transport_is_newline_delimited_and_silent_for_notifications(self) -> None:
        paths = resolve_paths(str(self.project), "config/diamond.toml", str(self.base / "state"))
        incoming = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
            "",
        ]))
        outgoing = io.StringIO()
        self.assertEqual(serve_stdio(paths, incoming, outgoing), 0)
        responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, 2])
        self.assertEqual(responses[1]["result"], {})
