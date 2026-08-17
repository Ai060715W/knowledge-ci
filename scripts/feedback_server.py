from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import discover_config_path, load_project_paths
from src.inject.context import record_feedback


PREVIEW_DIR = ROOT / "preview"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


class FeedbackRequestHandler(BaseHTTPRequestHandler):
    """Serve the Quill preview and collect Knowledge CI feedback into a JSONL log."""

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/feedback":
            self._handle_feedback(parsed.query)
        else:
            self._serve_static(parsed.path)

    def _handle_feedback(self, query: str) -> None:
        params = parse_qs(query)
        unit_id = (params.get("unit_id") or [""])[0].strip()
        file_path = (params.get("file") or [""])[0].strip()
        feedback = (params.get("feedback") or [""])[0].strip()
        if feedback not in {"useful", "improve"}:
            self._respond(400, "text/plain; charset=utf-8", "feedback 参数必须是 useful 或 improve。")
            return
        record = record_feedback(self.server.feedback_file, unit_id, file_path, feedback)
        body = (
            "<!doctype html><meta charset='utf-8'><title>Knowledge CI 反馈</title>"
            f"<body><h2>已记录反馈：{feedback}</h2>"
            f"<pre>{json.dumps(record, ensure_ascii=False, indent=2)}</pre>"
            "<p><a href='/'>返回预览器</a></p></body>"
        )
        self._respond(200, "text/html; charset=utf-8", body)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        preview_root = PREVIEW_DIR.resolve()
        target = (PREVIEW_DIR / relative).resolve()
        if not target.is_relative_to(preview_root) or not target.is_file():
            self._respond(404, "text/plain; charset=utf-8", "Not found")
            return
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _respond(self, status: int, content_type: str, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    feedback_file: str | Path | None = None,
) -> ThreadingHTTPServer:
    if feedback_file:
        feedback_path = Path(feedback_file)
    else:
        discovered = discover_config_path()
        feedback_path = (
            load_project_paths(discovered)["feedback_path"] if discovered else ROOT / "data" / "feedback.jsonl"
        )
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), FeedbackRequestHandler)
    server.feedback_file = feedback_path
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the Quill preview and collect Knowledge CI feedback.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address.")
    parser.add_argument("--port", type=int, default=8080, help="Bind port.")
    parser.add_argument(
        "--feedback-file",
        default=None,
        help="JSON Lines feedback log path (default: .knowledge-ci/data/feedback.jsonl when configured).",
    )
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.feedback_file)
    host, port = server.server_address
    print(f"Knowledge CI 预览与反馈服务：http://{host}:{port}")
    print(f"Quill 预览：http://{host}:{port}/?delta=<preview_delta>")
    print(f"反馈日志：{server.feedback_file}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
