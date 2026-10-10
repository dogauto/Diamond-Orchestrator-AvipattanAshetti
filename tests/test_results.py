import asyncio
import json

from diamond.adapters import build_invocation
from diamond.domain import Provider
from diamond.results import ParseStatus, parse_result
from diamond.supervisor import ExecutionResult, ExecutionStatus, run
from support import ProjectCase


class ResultTests(ProjectCase):
    def test_text_provider_is_normalized_without_inventing_identity(self) -> None:
        invocation = build_invocation(Provider.OMNIRUSH, "omnirush", "prompt")
        result = parse_result(invocation, ExecutionResult(ExecutionStatus.SUCCEEDED, 0, "answer\n", ""))
        self.assertEqual((result.status, result.content, result.reported_model, result.session_id),
                         (ParseStatus.VALID, "answer", None, None))

    def test_json_and_jsonl_identity_fields_are_preserved(self) -> None:
        invocation = build_invocation(Provider.CODEX, "codex", "prompt")
        execution = ExecutionResult(ExecutionStatus.SUCCEEDED, 0,
                                    '{"model":"gpt-test","session_id":"s1","result":"done"}', "")
        result = parse_result(invocation, execution)
        self.assertEqual((result.status, result.content, result.reported_model, result.session_id),
                         (ParseStatus.VALID, "done", "gpt-test", "s1"))
        jsonl = ExecutionResult(ExecutionStatus.SUCCEEDED, 0,
                                '{"type":"started"}\n{"model":"m","text":"final"}\n', "")
        result = parse_result(invocation, jsonl)
        self.assertEqual((result.content, result.reported_model), ("final", "m"))
        for payload in result.payloads:
            json.loads(payload)

    def test_nested_copilot_events_surface_assistant_content_and_identity(self) -> None:
        invocation = build_invocation(Provider.COPILOT, "copilot", "prompt")
        execution = ExecutionResult(
            ExecutionStatus.SUCCEEDED,
            0,
            '{"type":"session.start","data":{"sessionId":"s1","selectedModel":"auto"}}\n'
            '{"type":"assistant.message","data":{"content":"marker", "model":"m1"}}\n'
            '{"type":"session.shutdown","data":{"currentModel":"m1"}}\n',
            "",
        )
        result = parse_result(invocation, execution)
        self.assertEqual((result.status, result.content, result.reported_model, result.session_id),
                         (ParseStatus.VALID, "marker", "m1", "s1"))

    def test_copilot_assistant_message_chunks_are_reassembled_in_order(self) -> None:
        invocation = build_invocation(Provider.COPILOT, "copilot", "prompt")
        execution = ExecutionResult(
            ExecutionStatus.SUCCEEDED,
            0,
            '{"type":"assistant.message","data":{"messageId":"m1","chunkIndex":1,"content":"OK"}}\n'
            '{"type":"assistant.message","data":{"messageId":"m1","chunkIndex":0,"content":"SMOKE_"}}',
            "",
        )
        result = parse_result(invocation, execution)
        self.assertEqual((result.status, result.content), (ParseStatus.VALID, "SMOKE_OK"))

    def test_json_without_content_is_empty_even_when_identity_exists(self) -> None:
        invocation = build_invocation(Provider.COPILOT, "copilot", "prompt")
        execution = ExecutionResult(ExecutionStatus.SUCCEEDED, 0,
                                    '{"type":"session.start","data":{"sessionId":"s1"}}', "")
        result = parse_result(invocation, execution)
        self.assertEqual((result.status, result.content, result.session_id),
                         (ParseStatus.EMPTY, None, "s1"))

    def test_gemini_response_and_stats_model_are_normalized(self) -> None:
        invocation = build_invocation(Provider.GEMINI, "gemini", "prompt")
        execution = ExecutionResult(
            ExecutionStatus.SUCCEEDED,
            0,
            json.dumps({
                "session_id": "g1",
                "response": "marker",
                "stats": {"models": {
                    "gemini-3.1-pro-preview-customtools": {"api": {"totalErrors": 1}},
                    "gemini-3-flash-preview": {"api": {"totalErrors": 0}},
                }},
            }),
            "",
        )
        result = parse_result(invocation, execution)
        self.assertEqual((result.status, result.content, result.reported_model, result.session_id),
                         (ParseStatus.VALID, "marker", "gemini-3-flash-preview", "g1"))

    def test_antigravity_json_envelope_uses_conversation_id(self) -> None:
        invocation = build_invocation(Provider.ANTIGRAVITY, "agy", "prompt")
        execution = ExecutionResult(
            ExecutionStatus.SUCCEEDED, 0,
            json.dumps({"conversation_id": "a1", "status": "SUCCESS", "response": "marker"}), "")
        result = parse_result(invocation, execution)
        self.assertEqual((result.status, result.content, result.session_id),
                         (ParseStatus.VALID, "marker", "a1"))

    def test_antigravity_stream_events_and_structured_output_are_normalized(self) -> None:
        invocation = build_invocation(
            Provider.ANTIGRAVITY, "agy", "prompt", stream=True,
            json_schema='{"type":"object"}')
        execution = ExecutionResult(
            ExecutionStatus.SUCCEEDED,
            0,
            '{"event":"init","conversation_id":"a2","init":{"model":"gemini-test"}}\n'
            '{"event":"step_update","step_update":{"text_delta":"partial"}}\n'
            '{"event":"result","result":{"conversation_id":"a2","status":"SUCCESS",'
            '"response":"final","structured_output":{"summary":"ok"}}}',
            "",
        )
        result = parse_result(invocation, execution)
        self.assertEqual((result.status, result.content, result.reported_model, result.session_id,
                          result.structured_output),
                         (ParseStatus.VALID, "final", "gemini-test", "a2", '{"summary":"ok"}'))

    def test_antigravity_stream_provider_error_is_not_success(self) -> None:
        invocation = build_invocation(Provider.ANTIGRAVITY, "agy", "prompt", stream=True)
        execution = ExecutionResult(
            ExecutionStatus.SUCCEEDED,
            0,
            '{"event":"result","result":{"conversation_id":"a3","status":"ERROR",'
            '"error":"authentication required"}}',
            "",
        )
        result = parse_result(invocation, execution)
        self.assertEqual((result.status, result.session_id, result.detail),
                         (ParseStatus.PROVIDER_ERROR, "a3", "authentication required"))

    def test_malformed_empty_and_execution_failure_are_not_success(self) -> None:
        invocation = build_invocation(Provider.GEMINI, "gemini", "prompt")
        for execution, expected in ((ExecutionResult(ExecutionStatus.SUCCEEDED, 0, "", ""), ParseStatus.EMPTY),
                                    (ExecutionResult(ExecutionStatus.SUCCEEDED, 0, "not-json", ""), ParseStatus.MALFORMED),
                                    (ExecutionResult(ExecutionStatus.NONZERO, 2, "", "failed"), ParseStatus.EXECUTION_FAILED)):
            with self.subTest(expected=expected):
                self.assertEqual(parse_result(invocation, execution).status, expected)

    def test_supervisor_result_round_trip_uses_fake_only(self) -> None:
        executable = self.fake_executable("json-result", "print('{\\\"model\\\":\\\"fake\\\",\\\"text\\\":\\\"ok\\\"}')")
        invocation = build_invocation(Provider.GEMINI, str(executable), "prompt")
        execution = asyncio.run(run(invocation, self.project, timeout_seconds=2, output_limit=256))
        result = parse_result(invocation, execution)
        self.assertEqual((result.status, result.reported_model, result.content),
                         (ParseStatus.VALID, "fake", "ok"))
