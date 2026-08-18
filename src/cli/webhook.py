from __future__ import annotations

"""kc webhook: run the event-trigger server for push/MR events."""

import argparse
from pathlib import Path

from src.config import CONFIG_DEFAULTS, load_settings, resolve_config_path
from src.webhook.server import create_server


HELP = "Serve the push/MR webhook endpoint."


def build_parser(add_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve Knowledge CI webhook endpoints for push/MR events.",
        add_help=add_help,
    )
    parser.add_argument("--host", default=None, help="Bind address (default: webhook.bind_host or 127.0.0.1).")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: webhook.bind_port or 8090).")
    parser.add_argument(
        "--secret",
        default=None,
        help="GitHub webhook secret for X-Hub-Signature-256 verification (overrides webhook.secret).",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Run without a secret. Events are accepted unsigned; for local testing only.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Single-repo mode: local path of the checkout that receives events.",
    )
    parser.add_argument(
        "--repo-name",
        default=None,
        help="Single-repo mode: full repository name to expect in payloads (e.g. owner/repo).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to the server's .knowledge-ci/config.yaml (webhook section + repos mapping).",
    )
    return parser


def _single_repo_resolver(repo_name: str, repo_path: Path):
    def resolver(event: dict) -> dict | None:
        if event.get("repo_full_name") != repo_name:
            return None
        config_path = repo_path / ".knowledge-ci" / "config.yaml"
        return {"path": str(repo_path), "config_path": str(config_path)}

    return resolver


def _config_repo_resolver(repos: dict[str, str]):
    def resolver(event: dict) -> dict | None:
        full_name = event.get("repo_full_name", "")
        local_path = repos.get(full_name)
        if not local_path:
            return None
        path = Path(local_path).resolve()
        return {"path": str(path), "config_path": str(path / ".knowledge-ci" / "config.yaml")}

    return resolver


def run(args: argparse.Namespace) -> int:
    settings = CONFIG_DEFAULTS
    if args.config:
        config_path = resolve_config_path(args.config)
        settings = load_settings(config_path)

    webhook_settings = settings.get("webhook", {})
    host = args.host or webhook_settings.get("bind_host", "127.0.0.1")
    port = args.port or int(webhook_settings.get("bind_port", 8090))
    secret = args.secret if args.secret is not None else webhook_settings.get("secret", "")

    if args.repo:
        if not args.repo_name:
            print("--repo-name <owner/repo> is required with --repo (single-repo mode).")
            return 1
        resolver = _single_repo_resolver(args.repo_name, Path(args.repo).resolve())
    else:
        repos = {str(key): str(value) for key, value in webhook_settings.get("repos", {}).items()}
        if not repos:
            print(
                "No repositories configured. Provide --repo/--repo-name (single-repo mode) "
                "or a webhook.repos mapping in the config file."
            )
            return 1
        resolver = _config_repo_resolver(repos)

    if not secret and not args.insecure:
        print(
            "Refusing to start without a webhook secret. Configure webhook.secret, pass --secret, "
            "or use --insecure for local testing only."
        )
        return 1

    server = create_server(host=host, port=port, secret=secret, repo_resolver=resolver)
    bound_host, bound_port = server.server_address
    print(f"Knowledge CI webhook server: http://{bound_host}:{bound_port}")
    print(f"  POST /webhook/push  POST /webhook/mr  GET /health")
    print(f"  signature verification: {'ON' if secret else 'OFF (--insecure)'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
