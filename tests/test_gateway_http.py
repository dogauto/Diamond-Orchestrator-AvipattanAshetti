import json
import multiprocessing
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import TestCase
from unittest.mock import patch

from diamond.errors import ProviderAuthError, ProviderError
from diamond.gateway_http import MAX_RESPONSE_BYTES, request_json
from support import ProjectCase

KEY = "fake-gateway-" + "credential-41"


class GatewayServer:
    def __init__(self, *, status=200, payload=None, raw=None, content_type="application/json",
                 extra_headers=None, delay=0, drip=False):
        self.requests = []
        body = raw if raw is not None else json.dumps(payload if payload is not None else {"data": [{"id": "model-a"}]}).encode()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.requests.append((self.path, self.headers.get("X-API-Key"), self.headers.get("Authorization"), None))
                self.reply()

            def do_POST(self):
                received = self.rfile.read(int(self.headers["Content-Length"]))
                owner.requests.append((self.path, self.headers.get("X-API-Key"), self.headers.get("Authorization"), json.loads(received)))
                self.reply()

            def reply(self):
                try:
                    time.sleep(delay)
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    for key, value in (extra_headers or {}).items():
                        self.send_header(key, value)
                    self.end_headers()
                    if drip:
                        for byte in body:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.05)
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class GatewayHTTPTests(TestCase):
    def server(self, **kwargs):
        server = GatewayServer(**kwargs)
        self.addCleanup(server.close)
        return server

    def test_real_get_and_post_contract(self):
        server = self.server()
        self.assertEqual(request_json(server.url + "/v1/models", KEY, None, 2), {"data": [{"id": "model-a"}]})
        request_json(server.url + "/v1/chat/completions", KEY, {"model": "model-a"}, 2)
        self.assertEqual(server.requests, [("/v1/models", KEY, None, None),
                                          ("/v1/chat/completions", KEY, None, {"model": "model-a"})])

    def test_auth_rate_limit_server_error_omit_private_body_and_do_not_retry(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                server = self.server(status=status, raw=KEY.encode())
                expected = ProviderAuthError if status in {401, 403} else ProviderError
                with self.assertRaises(expected) as caught:
                    request_json(server.url + "/v1/models", KEY, None, 2)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual(len(server.requests), 1)

    def test_redirect_never_forwards_authorization_to_second_server(self):
        destination = self.server()
        source = self.server(status=302, extra_headers={"Location": destination.url + "/capture"})
        with self.assertRaisesRegex(ProviderError, "redirects are disabled"):
            request_json(source.url, KEY, None, 2)
        self.assertEqual(destination.requests, [])

    def test_ambient_proxy_is_not_used(self):
        proxy = self.server()
        destination = self.server()
        with patch.dict("os.environ", {"http_proxy": proxy.url, "HTTP_PROXY": proxy.url,
                                      "ALL_PROXY": proxy.url, "no_proxy": "", "NO_PROXY": ""}):
            request_json(destination.url, KEY, None, 2)
        self.assertEqual(len(destination.requests), 1)
        self.assertEqual(proxy.requests, [])
        self.assertEqual(destination.requests[0][2], None)

    def test_response_limits_encoding_truncation_and_json_errors(self):
        cases = [{"extra_headers": {"Content-Length": str(MAX_RESPONSE_BYTES + 1)}},
                 {"raw": b"x" * (MAX_RESPONSE_BYTES + 1)}, {"extra_headers": {"Content-Length": "invalid"}},
                 {"extra_headers": {"Content-Length": "1000"}, "raw": b"{}"},
                 {"content_type": "text/html"}, {"extra_headers": {"Content-Encoding": "gzip"}},
                 {"raw": b"\xff"}, {"raw": b"not-json"}, {"raw": b"[" * 2000}, {"raw": b'{"value":NaN}'}]
        for kwargs in cases:
            with self.subTest(kwargs=str(kwargs)[:100]):
                server = self.server(**kwargs)
                with self.assertRaises(ProviderError):
                    request_json(server.url, KEY, None, 2)

    def test_headers_and_slow_drip_body_have_wall_clock_deadline_and_no_worker_leak(self):
        before = {child.pid for child in multiprocessing.active_children()}
        for options in ({"delay": 2}, {"drip": True, "raw": b" " * 2000}):
            with self.subTest(options=options):
                server = self.server(**options)
                start = time.monotonic()
                with self.assertRaisesRegex(ProviderError, "deadline"):
                    request_json(server.url, KEY, None, 0.4)
                self.assertLess(time.monotonic() - start, 1.5)
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, before)


class GatewayCLIIntegrationTests(ProjectCase):
    def test_black_box_configure_models_confirmed_smoke_and_no_sqlite(self):
        payload = {"model": "model-a", "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9}}
        models = GatewayServer()
        smoke = GatewayServer(payload=payload)
        self.addCleanup(models.close)
        self.addCleanup(smoke.close)
        environment = {"FREELLMAPI_API_KEY": KEY}
        configured = self.cli_process("provider", "configure", "freellmapi", "--url", models.url, "--json")
        self.assertEqual(configured.returncode, 0, configured.stdout + configured.stderr)
        discovered = self.cli_process("provider", "models", "freellmapi", "--json", environment=environment)
        self.assertEqual(discovered.returncode, 0, discovered.stdout + discovered.stderr)
        self.assertEqual(json.loads(discovered.stdout)["result"]["models"], ["model-a"])
        configured = self.cli_process("provider", "configure", "freellmapi", "--url", smoke.url,
                                      "--force", "--model", "model-a", "--json")
        self.assertEqual(configured.returncode, 0, configured.stdout + configured.stderr)
        tested = self.cli_process("provider", "test", "freellmapi", "--confirm", "--json", environment=environment)
        self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
        result = json.loads(tested.stdout)["result"]
        self.assertTrue(result["marker_matched"])
        self.assertFalse(result["model_mismatch"])
        self.assertEqual(len(smoke.requests), 1)
        self.assertFalse(smoke.requests[0][3]["stream"])
        self.assertFalse((self.project / ".diamond" / "diamond.sqlite3").exists())
        self.assertNotIn(KEY, tested.stdout + tested.stderr + discovered.stdout + discovered.stderr)

    def test_black_box_http_errors_are_stable_json_and_nonzero(self):
        server = GatewayServer(status=401, raw=KEY.encode())
        self.addCleanup(server.close)
        self.cli_process("provider", "configure", "freellmapi", "--url", server.url)
        result = self.cli_process("provider", "models", "freellmapi", "--json", environment={"FREELLMAPI_API_KEY": KEY})
        self.assertEqual(result.returncode, 6)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "PROVIDER_AUTH_ERROR")
        self.assertNotIn(KEY, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
