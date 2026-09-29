"""``botgraph-api``: serve the API and manage dashboard users.

botgraph-api create-user --username admin --role admin     # prompts for the password
botgraph-api set-password | disable-user | revoke-tokens --username <name>
botgraph-api serve [--host 127.0.0.1] [--port 8000] [--metrics-port 9100]
"""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

import uvicorn

from botgraph_api.app import create_app
from botgraph_api.auth import load_secret
from botgraph_api.seed import seed
from botgraph_ml.config import repo_path
from botgraph_stream.cli import default_db_url
from botgraph_stream.logs import configure_logging
from botgraph_stream.metrics import serve_metrics
from botgraph_stream.store import ROLES, Store


def _store(args: argparse.Namespace) -> Store:
    return Store(args.db or os.environ.get("BOTGRAPH_DB_URL") or default_db_url())


def cmd_serve(args: argparse.Namespace) -> None:
    configure_logging()
    app = create_app(_store(args), load_secret(repo_path("data")))
    if args.metrics_port:
        serve_metrics(args.metrics_port).set_ready()
    # log_config=None: uvicorn logs through our handler; its access log is replaced by the
    # middleware's structured one (route template, request id, duration).
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("BOTGRAPH_TRUSTED_PROXIES", "127.0.0.1"),
    )


def cmd_create_user(args: argparse.Namespace) -> None:
    password = args.password or getpass.getpass(f"password for {args.username}: ")
    user = _store(args).create_user(args.username, password, args.role)
    print(f"created {user.role} {user.username}")


def cmd_set_password(args: argparse.Namespace) -> None:
    password = args.password or getpass.getpass(f"new password for {args.username}: ")
    if not _store(args).set_password(args.username, password):
        raise SystemExit(f"no user {args.username}")
    print(f"password changed; {args.username}'s existing sessions are signed out")


def cmd_disable_user(args: argparse.Namespace) -> None:
    if not _store(args).set_disabled(args.username, not args.enable):
        raise SystemExit(f"no user {args.username}")
    print(f"{args.username} {'enabled' if args.enable else 'disabled and signed out'}")


def cmd_revoke_tokens(args: argparse.Namespace) -> None:
    if not _store(args).revoke_tokens(args.username):
        raise SystemExit(f"no user {args.username}")
    print(f"every session of {args.username} is signed out")


def cmd_seed_demo(args: argparse.Namespace) -> None:
    if args.fresh and args.db and args.db.startswith("sqlite:///"):
        for suffix in ("", "-wal", "-shm"):
            Path(args.db.removeprefix("sqlite:///") + suffix).unlink(missing_ok=True)
    seed(_store(args), args.username, args.password)
    print(f"seeded demo data and {args.username} into {args.db or 'the default store'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="botgraph-api", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--db", help="SQLAlchemy URL (default: BOTGRAPH_DB_URL or data/botgraph.db)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--metrics-port",
        type=int,
        default=int(os.environ.get("BOTGRAPH_METRICS_PORT", "0")) or None,
        help="serve Prometheus /metrics on this port (env BOTGRAPH_METRICS_PORT)",
    )
    serve.set_defaults(func=cmd_serve)

    user = sub.add_parser("create-user", help="add a dashboard account")
    user.add_argument("--username", required=True)
    user.add_argument("--role", choices=ROLES, default="analyst")
    user.add_argument("--password", help="omit to be prompted (keeps it out of shell history)")
    user.set_defaults(func=cmd_create_user)

    pw = sub.add_parser("set-password", help="change a password (signs the user out)")
    pw.add_argument("--username", required=True)
    pw.add_argument("--password", help="omit to be prompted")
    pw.set_defaults(func=cmd_set_password)

    dis = sub.add_parser("disable-user", help="block an account and revoke its sessions")
    dis.add_argument("--username", required=True)
    dis.add_argument("--enable", action="store_true", help="re-enable instead")
    dis.set_defaults(func=cmd_disable_user)

    rev = sub.add_parser("revoke-tokens", help="sign a user out of every session")
    rev.add_argument("--username", required=True)
    rev.set_defaults(func=cmd_revoke_tokens)

    demo = sub.add_parser("seed-demo", help="synthetic demo data + an admin (UI dev, e2e tests)")
    demo.add_argument("--username", default="demo")
    demo.add_argument("--password", required=True)
    demo.add_argument("--fresh", action="store_true", help="delete the SQLite file first")
    demo.set_defaults(func=cmd_seed_demo)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
