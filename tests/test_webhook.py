import hashlib
import hmac
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from src.webhook.server import (
    EventParseError,
    create_server,
    parse_github_event,
    parse_github_pull_request,
    parse_github_push,
    parse_platform_event,
    verify_github_signature,
)
from src.webhook.pipeline import build_mr_comment, run_event_actions


def signed_headers(secret: str, raw_body: bytes) -> dict[str, str]:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


def init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)


def commit_all(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


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


class WebhookPipelineTest(unittest.TestCase):
    def test_build_mr_comment_includes_impact_freshness_and_patch_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            patches = root / "patches"
            patches.mkdir()
            patch = {
                "patch_id": "kp_1",
                "unit_id": "payment_retry",
                "delta_ops": [{"insert": "new"}],
            }
            (patches / "patch_kp_1.json").write_text(json.dumps(patch), encoding="utf-8")
            body = build_mr_comment(
                {"kind": "mr", "number": 7, "title": "change retry", "head_sha": "abc1234"},
                {"patches_path": patches},
                {
                    "impact_report": {
                        "changed_files": [
                            {
                                "path": "src/payment/retry.py",
                                "unit_id": "payment_retry",
                                "summary": {"functions": ["retry_payment"], "classes": [], "constants": []},
                            }
                        ]
                    },
                    "freshness_report": {
                        "summary": {"needs_llm": 1, "total": 1},
                        "units": [
                            {
                                "unit_id": "payment_retry",
                                "verdict": "partial_update",
                                "basis": "llm",
                                "layers": [{"layer": "time"}, {"layer": "ast"}],
                                "actions": ["patch_written: patch_kp_1.json"],
                            }
                        ],
                    },
                },
            )

        self.assertIn("Knowledge CI MR Summary", body)
        self.assertIn("payment_retry", body)
        self.assertIn("retry_payment", body)
        self.assertIn("partial_update", body)
        self.assertIn("http://localhost:8080/?delta=", body)

    def test_mr_comment_action_writes_local_markdown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            init_git_repo(root)
            (root / "mod.py").write_text("MAX = 5\n", encoding="utf-8")
            first = commit_all(root, "initial")
            knowledge_dir = root / ".knowledge-ci"
            data_dir = knowledge_dir / "data"
            reports = data_dir / "reports"
            patches = data_dir / "patches"
            reports.mkdir(parents=True)
            patches.mkdir()
            registry = data_dir / "registry.json"
            registry.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "last_updated": "",
                        "units": [
                            {
                                "id": "u1",
                                "title": "Unit",
                                "status": "active",
                                "version": 1,
                                "scope": {"files": ["mod.py"], "symbols": []},
                                "knowledge_delta": {"ops": [{"insert": "MAX is 5"}]},
                                "last_verified": None,
                                "code_hash": first[:8],
                                "evidence": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = knowledge_dir / "config.yaml"
            config.write_text(
                "\n".join(
                    [
                        'project_path: ".."',
                        'registry_path: "data/registry.json"',
                        'reports_path: "data/reports"',
                        'patches_path: "data/patches"',
                        "webhook:",
                        "  events:",
                        "    mr: [analyze, freshness, comment]",
                        "  comment_dry_run: true",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "mod.py").write_text("MAX = 3\n", encoding="utf-8")
            head = commit_all(root, "change")

            event = {"kind": "mr", "number": 7, "title": "change", "head_sha": head}
            with mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
                results = run_event_actions(event, {"config_path": str(config)})

            comment_files = list(reports.glob("mr_comment_*.md"))
            body = comment_files[0].read_text(encoding="utf-8")

        self.assertEqual([item["name"] for item in results], ["analyze", "freshness", "comment"])
        self.assertEqual(len(comment_files), 1)
        self.assertIn("u1", body)
        self.assertIn("needs_llm", body)
        self.assertIn("Nothing was landed automatically", body)


if __name__ == "__main__":
    unittest.main()
