from __future__ import annotations

"""stdlib webhook server: GitHub push / MR events -> local pipeline runs.

Security model:

- When a secret is configured, every POST must carry a valid
  ``X-Hub-Signature-256`` HMAC (timing-safe comparison); otherwise the
  request is rejected with 401.
- The CLI refuses to start without a secret unless ``--insecure`` is passed
  explicitly.

Platform model:

- v1 parses GitHub push and pull_request payloads. A ``parse_platform_event``
  dispatcher is the extension point for other platforms (e.g. GitLab); unknown
  payloads are rejected with 400 and a clear message.
"""

import hashlib
import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit

__all__ = [
    "EventParseError",
    "WebhookRequestHandler",
    "create_server",
    "parse_github_event",
    "parse_platform_event",
    "verify_github_signature",
]


class EventParseError(ValueError):
    """Raised when a webhook payload cannot be mapped to a supported event."""


def verify_github_signature(secret: str, signature_header: str, raw_body: bytes) -> bool:
    """Verify a GitHub ``X-Hub-Signature-256`` header against the raw body.

    Timing-safe. Missing/malformed headers and empty secrets fail closed.
    """
    if not secret or not signature_header:
        return False
    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header[len(prefix):]
    return hmac.compare_digest(expected, provided)


def _repo_full_name(payload: dict[str, Any]) -> str:
    repository = payload.get("repository") or {}
    return str(repository.get("full_name", ""))


def parse_github_push(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a GitHub push payload to a normalized event dict."""
    if not _repo_full_name(payload):
        raise EventParseError("payload is missing repository.full_name")
    head_commit = payload.get("head_commit") or {}
    head_sha = str(payload.get("after") or head_commit.get("id") or "")
    if not head_sha:
        raise EventParseError("payload is missing after/head_commit.id")
    return {
        "kind": "push",
        "platform": "github",
        "repo_full_name": _repo_full_name(payload),
        "ref": str(payload.get("ref", "")),
        "before_sha": str(payload.get("before", "")),
        "head_sha": head_sha,
        "pusher": str((payload.get("pusher") or {}).get("name", "")),
        "commits": [
            {
                "id": str(commit.get("id", "")),
                "message": str(commit.get("message", ""))[:200],
            }
            for commit in (payload.get("commits") or [])
        ],
    }


def parse_github_pull_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a GitHub pull_request payload to a normalized event dict."""
    if not _repo_full_name(payload):
        raise EventParseError("payload is missing repository.full_name")
    pull_request = payload.get("pull_request") or {}
    head = pull_request.get("head") or {}
    head_sha = str(head.get("sha", ""))
    if not head_sha:
        raise EventParseError("payload is missing pull_request.head.sha")
    return {
        "kind": "mr",
        "platform": "github",
        "repo_full_name": _repo_full_name(payload),
        "ref": str((pull_request.get("base") or {}).get("ref", "")),
        "head_sha": head_sha,
        "number": pull_request.get("number"),
        "action": str(payload.get("action", "")),
        "title": str(pull_request.get("title", ""))[:200],
    }


def parse_platform_event(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    """Dispatch a payload to the platform parser for the request kind.

    Extension point: add other platforms (e.g. GitLab) here without touching
    the HTTP layer.
    """
    if kind == "push":
        return parse_github_push(payload)
    if kind == "mr":
        return parse_github_pull_request(payload)
    raise EventParseError(f"unsupported event kind: {kind}")


def parse_github_event(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    """Compatibility alias for ``parse_platform_event`` (GitHub v1)."""
    return parse_platform_event(payload, kind)


class WebhookRequestHandler(BaseHTTPRequestHandler):
    """Handle webhook POSTs; the actual work is delegated to the server."""

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self._respond(200, "application/json; charset=utf-8", '{"status": "ok"}\n')
        else:
            self._respond(404, "text/plain; charset=utf-8", "Not found\n")

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        kind = {
            "/webhook/push": "push",
            "/webhook/mr": "mr",
        }.get(parsed.path)
        if kind is None:
            self._respond(404, "text/plain; charset=utf-8", "Not found\n")
            return

        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._respond(400, "text/plain; charset=utf-8", "Bad Content-Length\n")
            return
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        server: WebhookServer = self.server  # type: ignore[assignment]
        if server.secret and not verify_github_signature(
            server.secret, self.headers.get("X-Hub-Signature-256", ""), raw_body
        ):
            self._respond(401, "text/plain; charset=utf-8", "Invalid signature\n")
            return

        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, "text/plain; charset=utf-8", "Invalid JSON body\n")
            return

        try:
            event = parse_platform_event(payload, kind)
        except EventParseError as error:
            self._respond(400, "text/plain; charset=utf-8", f"Unsupported payload: {error}\n")
            return

        repo_info = server.repo_resolver(event)
        if repo_info is None:
            self._respond(
                200,
                "application/json; charset=utf-8",
                json.dumps(
                    {
                        "status": "ignored",
                        "reason": f"no local checkout configured for {event.get('repo_full_name')}",
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
            return

        results = server.runner(event, repo_info)
        self._respond(
            200,
            "application/json; charset=utf-8",
            json.dumps(
                {
                    "status": "ok",
                    "kind": event["kind"],
                    "repo_full_name": event["repo_full_name"],
                    "head_sha": event["head_sha"],
                    "actions": results,
                },
                ensure_ascii=False,
            )
            + "\n",
        )

    def _respond(self, status: int, content_type: str, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")


RepoResolver = Callable[[dict[str, Any]], dict[str, Any] | None]
Runner = Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]


class WebhookServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the webhook configuration."""

    def __init__(
        self,
        address: tuple[str, int],
        secret: str,
        repo_resolver: RepoResolver,
        runner: Runner,
    ) -> None:
        super().__init__(address, WebhookRequestHandler)
        self.secret = secret
        self.repo_resolver = repo_resolver
        self.runner = runner


def create_server(
    host: str = "127.0.0.1",
    port: int = 8090,
    secret: str = "",
    repo_resolver: RepoResolver | None = None,
    runner: Runner | None = None,
) -> WebhookServer:
    """Build a webhook server. ``repo_resolver`` maps events to local checkouts
    (``{"path": ..., "config_path": ...}`` or None); ``runner`` executes the
    pipeline for one event. Both are injectable for tests.
    """
    from src.webhook.pipeline import run_event_actions

    def default_resolver(event: dict[str, Any]) -> dict[str, Any] | None:
        return None

    server = WebhookServer(
        (host, port),
        secret,
        repo_resolver=repo_resolver or default_resolver,
        runner=runner or run_event_actions,
    )
    return server
