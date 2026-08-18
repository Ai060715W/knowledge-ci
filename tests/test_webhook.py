import hashlib
import hmac
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from src.webhook.server import (
    EventParseError,
    create_server,
    parse_github_event,
    parse_github_pull_request,
    parse_github_push,
    parse_platform_event,
    verify_github_signature,
)


def signed_headers(secret: str, raw_body: bytes) -> dict[str, str]:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


class SignatureTest(unittest.TestCase):
    def test_valid_signature_passes(self):
        body = b'{"x": 1}'
        self.assertTrue(verify_github_signature("s3cret", signed_headers("s3cret", body)["X-Hub-Signature-256"], body))

    def test_wrong_secret_fails(self):
        body = b'{"x": 1}'
        header = signed_headers("real", body)["X-Hub-Signature-256"]
        self.assertFalse(verify_github_signature("other", header, body))

    def test_tampered_body_fails(self):
        body = b'{"x": 1}'
        header = signed_headers("s3cret", body)["X-Hub-Signature-256"]
        self.assertFalse(verify_github_signature("s3cret", header, b'{"x": 2}'))

    def test_missing_or_malformed_header_fails(self):
        self.assertFalse(verify_github_signature("s3cret", "", b"body"))
        self.assertFalse(verify_github_signature("s3cret", "sha1=abcd", b"body"))

    def test_empty_secret_fails_closed(self):
        self.assertFalse(verify_github_signature("", signed_headers("", b"x")["X-Hub-Signature-256"], b"x"))


class EventParsingTest(unittest.TestCase):
    def test_push_payload(self):
        event = parse_github_push(
            {
                "repository": {"full_name": "owner/repo"},
                "ref": "refs/heads/main",
                "before": "aaa",
                "after": "bbb",
                "pusher": {"name": "dev"},
                "commits": [{"id": "bbb", "message": "fix"}],
            }
        )
        self.assertEqual(event["kind"], "push")
        self.assertEqual(event["repo_full_name"], "owner/repo")
        self.assertEqual(event["head_sha"], "bbb")

    def test_push_payload_missing_sha_rejected(self):
        with self.assertRaises(EventParseError):
            parse_github_push({"repository": {"full_name": "owner/repo"}})

    def test_pull_request_payload(self):
        event = parse_github_pull_request(
            {
                "action": "synchronize",
                "repository": {"full_name": "owner/repo"},
                "pull_request": {
                    "number": 7,
                    "title": "feat",
                    "base": {"ref": "main"},
                    "head": {"sha": "ccc"},
                },
            }
        )
        self.assertEqual(event["kind"], "mr")
        self.assertEqual(event["head_sha"], "ccc")
        self.assertEqual(event["number"], 7)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(EventParseError):
            parse_platform_event({"repository": {"full_name": "x/y"}}, "comment")

    def test_parse_github_event_alias(self):
        event = parse_github_event({"repository": {"full_name": "a/b"}, "after": "d"}, "push")
        self.assertEqual(event["platform"], "github")


class WebhookServerTest(unittest.TestCase):
    def make_server(self, secret="", resolver=None, runner=None):
        server = create_server("127.0.0.1", 0, secret=secret, repo_resolver=resolver, runner=runner)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def post(self, port: int, path: str, payload: dict, headers: dict | None = None) -> tuple[int, dict]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=body,
            headers=headers or {},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, {}

    def test_health_endpoint(self):
        server, thread = self.make_server()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["status"], "ok")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_signed_push_runs_pipeline_with_parsed_event(self):
        captured = {}

        def resolver(event):
            captured["event"] = event
            return {"path": "/tmp/repo", "config_path": "/tmp/repo/.knowledge-ci/config.yaml"}

        def runner(event, repo_info):
            captured["runner_event"] = event
            captured["repo_info"] = repo_info
            return [{"name": "analyze", "ok": True, "detail": "impact report: x.json"}]

        server, thread = self.make_server(secret="s3cret", resolver=resolver, runner=runner)
        try:
            port = server.server_address[1]
            payload = {"repository": {"full_name": "owner/repo"}, "after": "abc", "ref": "refs/heads/main"}
            body = json.dumps(payload).encode("utf-8")
            status, response = self.post(port, "/webhook/push", payload, headers=signed_headers("s3cret", body))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(captured["event"]["head_sha"], "abc")
        self.assertEqual(captured["runner_event"]["kind"], "push")
        self.assertEqual(captured["repo_info"]["config_path"].endswith("config.yaml"), True)

    def test_bad_signature_rejected_401(self):
        server, thread = self.make_server(secret="s3cret")
        try:
            port = server.server_address[1]
            status, _ = self.post(port, "/webhook/push", {"repository": {"full_name": "x/y"}, "after": "a"}, headers={"X-Hub-Signature-256": "sha256=deadbeef"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(status, 401)

    def test_insecure_server_accepts_unsigned(self):
        server, thread = self.make_server(secret="", resolver=lambda e: None, runner=lambda e, r: [])
        try:
            port = server.server_address[1]
            status, response = self.post(port, "/webhook/mr", {"repository": {"full_name": "x/y"}, "pull_request": {"head": {"sha": "a"}}})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ignored")

    def test_unknown_repo_ignored(self):
        server, thread = self.make_server(secret="", resolver=lambda e: None, runner=lambda e, r: [])
        try:
            port = server.server_address[1]
            status, response = self.post(port, "/webhook/push", {"repository": {"full_name": "x/y"}, "after": "a"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ignored")

    def test_unknown_endpoint_404(self):
        server, thread = self.make_server(secret="")
        try:
            port = server.server_address[1]
            status, _ = self.post(port, "/webhook/other", {"x": 1})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(status, 404)

    def test_invalid_json_body_400(self):
        server, thread = self.make_server(secret="")
        try:
            port = server.server_address[1]
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/webhook/push", data=b"not json", method="POST"
            )
            try:
                urllib.request.urlopen(request, timeout=10)
            except urllib.error.HTTPError as error:
                self.assertEqual(error.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
