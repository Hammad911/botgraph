"""``botgraph-api``: serve the API and manage dashboard users.

botgraph-api create-user --username admin --role admin     # prompts for the password
botgraph-api serve [--host 127.0.0.1] [--port 8000]
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
from botgraph_stream.store import ROLES, Store


def _store(args: argparse.Namespace) -> Store:
    return Store(args.db or os.environ.get("BOTGRAPH_DB_URL") or default_db_url())


def cmd_serve(args: argparse.Namespace) -> None:
    app = create_app(_store(args), load_secret(repo_path("data")))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def cmd_create_user(args: argparse.Namespace) -> None:
    password = args.password or getpass.getpass(f"password for {args.username}: ")
    user = _store(args).create_user(args.username, password, args.role)
    print(f"created {user.role} {user.username}")


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
    serve.set_defaults(func=cmd_serve)

    user = sub.add_parser("create-user", help="add a dashboard account")
    user.add_argument("--username", required=True)
    user.add_argument("--role", choices=ROLES, default="analyst")
    user.add_argument("--password", help="omit to be prompted (keeps it out of shell history)")
    user.set_defaults(func=cmd_create_user)

    demo = sub.add_parser("seed-demo", help="synthetic demo data + an admin (UI dev, e2e tests)")
    demo.add_argument("--username", default="demo")
    demo.add_argument("--password", required=True)
    demo.add_argument("--fresh", action="store_true", help="delete the SQLite file first")
    demo.set_defaults(func=cmd_seed_demo)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
