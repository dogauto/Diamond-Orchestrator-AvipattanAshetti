import io
import json
import os
import stat
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import patch

from diamond.cli import main
from diamond.errors import ProviderAuthError, ProviderError, StorageError, ValidationError
from diamond.freellmapi import (FreeLLMAPIClient, FreeLLMAPIConfig, SMOKE_PROMPT,
                                load_gateway_config, normalize_base_url, save_gateway_config)
from support import ProjectCase

KEY = "fixture-" + "credential-27"


def completion(**extra):
    return {"model": "actual-model", "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 6, "completion_tokens": 1, "total_tokens": 7}, **extra}


class FreeLLMAPIUnitTests(TestCase):
    def client(self, url="http://127.0.0.1:31415", **kwargs):
        return FreeLLMAPIClient(FreeLLMAPIConfig(url), environment={"FREELLMAPI_API_KEY": KEY}, **kwargs)

    def test_url_normalization_and_invalid_inputs(self):
        for url, normalized in (("http://127.0.0.1:31415///", "http://127.0.0.1:31415"),
                                ("https://api.example.test/v1/", "https://api.example.test/v1"),
                                ("http://[::1]:31415", "http://[::1]:31415")):
            self.assertEqual(normalize_base_url(url), normalized)
        for value in (None, "", " http://localhost", "http://local\thost", "http://localhost\n",
                      "http://example.test", "ftp://localhost", "https://user:password@example.test",
                      "https://@example.test", "http://localhost?key=bad", "http://localhost#bad",
                      "http://localhost/a/../", "http://localhost/%2e%2e", "http://localhost/%252e%252e",
                      "http://localhost\\@example.test", "http://localhost:bad", "http://localhost:0",
                      "http://localhost:65536", "http://localhost/secret-path"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_base_url(value)

    def test_config_validation_rejects_aliases_and_injection(self):
        for model in ("Smartest", "auto", "DEFAULT", "bad model", "bad\nmodel", "", 1, "a" * 257):
            with self.subTest(model=model), self.assertRaises(ValidationError):
                FreeLLMAPIConfig("http://localhost", default_model=model)
        for name in ("", "lowercase", "BAD;echo", "NAME\n", None, "A" * 129):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                FreeLLMAPIConfig("http://localhost", api_key_env=name)
        for timeout in (0, -1, 31, True, float("nan"), float("inf"), "5"):
            with self.subTest(timeout=timeout), self.assertRaises(ValidationError):
                self.client(timeout_seconds=timeout)

    def test_request_only_passes_secret_to_transport_and_normalizes_v1_once(self):
        with patch("diamond.freellmapi.request_json", return_value={"data": [{"id": "local-model"}]}) as request:
            client = self.client("http://127.0.0.1:31415/v1")
            self.assertEqual(client.list_models(), ("local-model",))
            request.assert_called_once_with("http://127.0.0.1:31415/v1/models", KEY, None, 10.0)
            self.assertEqual(client.auth_status, "PRESENT_NOT_CHECKED")

    def test_missing_invalid_credentials_and_secret_configuration_fail_before_transport(self):
        with patch("diamond.freellmapi.request_json") as request:
            for key in ("", " ", "line\nbreak", "x\ty", "x" * 4097, "非ASCII"):
                client = FreeLLMAPIClient(FreeLLMAPIConfig("http://localhost"), environment={"FREELLMAPI_API_KEY": key})
                with self.assertRaises(ProviderAuthError):
                    client.list_models()
            with self.assertRaises(ValidationError):
                FreeLLMAPIClient(FreeLLMAPIConfig("http://localhost", default_model=KEY),
                                environment={"FREELLMAPI_API_KEY": KEY})
            request.assert_not_called()

    def test_models_shape_limits_invalid_or_secret_ids_fail_closed(self):
        for payload in ({"error": "failed"}, [], {"models": []}, {"data": [None]}, {"data": ["name"]},
                        {"data": [{"id": "bad\nmodel"}]}, {"data": [{"id": KEY}]},
                        {"data": [{"id": "m"}] * 257}):
            with self.subTest(payload=str(payload)[:50]), patch("diamond.freellmapi.request_json", return_value=payload):
                with self.assertRaises(ProviderError) as caught:
                    self.client().list_models()
                self.assertNotIn(KEY, str(caught.exception))
        with patch("diamond.freellmapi.request_json", return_value={"data": [{"id": "m"}, {"id": "m"}]}):
            self.assertEqual(self.client().list_models(), ("m",))
        with patch("diamond.freellmapi.request_json", return_value={"data": []}):
            self.assertEqual(self.client().list_models(), ())

    def test_smoke_reports_model_mismatch_latency_usage_and_no_tools(self):
        with patch("diamond.freellmapi.request_json", return_value=completion()) as request:
            result = self.client().smoke("requested-model").to_dict()
        self.assertEqual(result["reported_model"], "actual-model")
        self.assertTrue(result["model_mismatch"])
        self.assertTrue(result["marker_matched"])
        self.assertEqual(result["usage"]["total_tokens"], 7)
        self.assertGreaterEqual(result["latency_ms"], 0)
        self.assertEqual(result["fallback_status"], "NOT_REPORTED")
        self.assertNotIn(KEY, json.dumps(result))
        body = request.call_args.args[2]
        self.assertEqual(body["messages"], [{"role": "user", "content": SMOKE_PROMPT}])
        self.assertFalse(body["stream"])
        self.assertEqual(body["max_tokens"], 64)
        self.assertNotIn("tools", body)

    def test_smoke_preserves_unknown_model_usage_and_does_not_output_response(self):
        with patch("diamond.freellmapi.request_json", return_value=completion(model=None, usage=None)):
            result = self.client().smoke("m").to_dict()
            self.assertIsNone(result["reported_model"])
            self.assertIsNone(result["model_mismatch"])
            self.assertIsNone(result["usage"])
        response = completion(choices=[{"message": {"content": KEY}, "finish_reason": "stop"}])
        with patch("diamond.freellmapi.request_json", return_value=response):
            self.assertNotIn(KEY, json.dumps(self.client().smoke("m", "custom prompt").to_dict()))

    def test_malformed_empty_truncated_tool_or_error_completions_fail(self):
        bad_choices = [None, [], [None], [{"message": None}], [{"message": {"content": ""}, "finish_reason": "stop"}],
                       [{"message": {"content": "OK"}, "finish_reason": "length"}],
                       [{"message": {"content": "wrong"}, "finish_reason": "stop"}],
                       [{"message": {"content": "OK", "tool_calls": [{}]}, "finish_reason": "stop"}]]
        payloads = [None, [], {"error": "private"}, completion(model=KEY), completion(usage={"total_tokens": True})]
        payloads += [completion(choices=choices) for choices in bad_choices]
        for payload in payloads:
            with self.subTest(payload=payload), patch("diamond.freellmapi.request_json", return_value=payload):
                with self.assertRaises(ProviderError) as caught:
                    self.client().smoke("m")
                self.assertNotIn(KEY, str(caught.exception))
        with patch("diamond.freellmapi.request_json") as request:
            for model, prompt in (("auto", SMOKE_PROMPT), (KEY, SMOKE_PROMPT), ("m", KEY), ("m", "")):
                with self.assertRaises(ValidationError):
                    self.client().smoke(model, prompt)
            request.assert_not_called()


class FreeLLMAPIFileAndCliTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.path = self.project / ".diamond" / "freellmapi.json"
        self.config = FreeLLMAPIConfig("http://127.0.0.1:31415", default_model="local-model")

    def call(self, *arguments):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["--project", str(self.project), "--json", "provider", *arguments])
        return code, json.loads(stdout.getvalue())

    def test_configuration_is_atomic_nonsecret_owner_only_and_replace_failure_keeps_original(self):
        save_gateway_config(self.path, self.config)
        self.assertEqual(load_gateway_config(self.path), self.config)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        before = self.path.read_bytes()
        with patch("diamond.freellmapi.os.replace", side_effect=OSError("private detail")):
            with self.assertRaises(StorageError):
                save_gateway_config(self.path, FreeLLMAPIConfig("http://localhost"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_existing_and_dangling_file_symlinks_parent_escape_and_traversal_rejected(self):
        self.path.parent.mkdir()
        target = self.base / "target.json"
        for exists in (False, True):
            if exists:
                target.write_text("original")
            self.path.symlink_to(target)
            for operation in (lambda: load_gateway_config(self.path), lambda: save_gateway_config(self.path, self.config)):
                with self.assertRaises(ValidationError):
                    operation()
            self.path.unlink()
        self.assertEqual(target.read_text(), "original")
        link = self.base / "linked-state"
        link.symlink_to(self.path.parent, target_is_directory=True)
        for path in (link / "freellmapi.json", self.path.parent / ".." / "other.json"):
            with self.assertRaises(ValidationError):
                save_gateway_config(path, self.config)

    def test_bad_config_has_safe_errors(self):
        self.path.parent.mkdir()
        for content in ('{"base_url":', "[" * 2000, '{"base_url":"http://localhost","api_key":"private"}',
                        '{"base_url":"http://localhost","default_model":1}', " " * 16385):
            self.path.write_text(content)
            code, result = self.call("list")
            self.assertEqual(code, 2)
            self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
            self.assertNotIn("private", result["error"]["message"])

    def test_provider_list_has_no_process_network_or_state_side_effects_in_both_modes(self):
        with patch("diamond.cli.doctor", side_effect=AssertionError("must not probe")), \
             patch("diamond.freellmapi.request_json", side_effect=AssertionError("must not connect")):
            code, result = self.call("list")
            self.assertEqual(code, 0)
            self.assertEqual(len(result["result"]["providers"]), 7)
            self.assertFalse(result["result"]["providers"][-1]["configured"])
            human = self.cli_process("provider", "list", environment={"PATH": ""})
            self.assertEqual(human.returncode, 0, human.stderr)
            self.assertIn("freellmapi: configured=False", human.stdout)
        self.assertFalse(self.path.parent.exists())

    def test_configure_idempotency_force_list_and_no_secret_or_sqlite(self):
        args = ("configure", "freellmapi", "--url", self.config.base_url, "--model", "local-model")
        with patch.dict(os.environ, {"FREELLMAPI_API_KEY": KEY}):
            first = self.call(*args)
            self.assertEqual(first[0], 0)
            self.assertEqual(self.call(*args), first)
            self.assertEqual(self.call("configure", "freellmapi", "--url", "http://localhost")[0], 2)
            self.assertEqual(self.call("configure", "freellmapi", "--url", "http://localhost", "--force")[0], 0)
            code, result = self.call("list")
            self.assertEqual(code, 0)
            self.assertEqual(result["result"]["providers"][-1]["auth_status"], "PRESENT_NOT_CHECKED")
            self.assertNotIn(KEY, json.dumps(result))
            self.assertNotIn(KEY, self.path.read_text())
        self.assertFalse((self.path.parent / "diamond.sqlite3").exists())

    def test_confirm_explicit_model_and_timeout_guards_precede_network(self):
        save_gateway_config(self.path, FreeLLMAPIConfig("http://localhost"))
        with patch("diamond.freellmapi.request_json") as request:
            for args in (("--model", "m"), ("--prompt", "p"), ("--confirm",),
                         ("--confirm", "--model", "auto"), ("--timeout", "nan"),
                         ("--confirm", "--model", "m", "--prompt", "")):
                code, _ = self.call("test", "freellmapi", *args)
                self.assertEqual(code, 2, args)
            request.assert_not_called()

    def test_discovery_never_posts_and_smoke_uses_configured_model_without_catalog_fallback(self):
        save_gateway_config(self.path, self.config)
        with patch.dict(os.environ, {"FREELLMAPI_API_KEY": KEY}):
            for action in ("models", "test"):
                with patch("diamond.freellmapi.request_json", return_value={"data": []}) as request:
                    code, result = self.call(action, "freellmapi")
                    self.assertEqual(code, 0)
                    self.assertEqual(result["result"]["models"], [])
                    self.assertIsNone(request.call_args.args[2])
            with patch("diamond.freellmapi.request_json", return_value=completion()) as request:
                code, result = self.call("test", "freellmapi", "--confirm")
                self.assertEqual(code, 0)
                self.assertTrue(result["result"]["live_request"])
                request.assert_called_once()
                self.assertEqual(request.call_args.args[2]["model"], "local-model")

    def test_missing_credentials_exit_six_and_missing_config_exits_two(self):
        self.assertEqual(self.call("models", "freellmapi")[0], 2)
        save_gateway_config(self.path, self.config)
        with patch.dict(os.environ, {"FREELLMAPI_API_KEY": ""}):
            code, result = self.call("models", "freellmapi")
            self.assertEqual(code, 6)
            self.assertEqual(result["error"]["code"], "PROVIDER_AUTH_ERROR")
