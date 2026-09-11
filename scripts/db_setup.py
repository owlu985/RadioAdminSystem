#!/usr/bin/env python3
import argparse
import os

from app import create_app
from app.db_utils import ensure_schema
from app.models import db
from flask_migrate import Migrate, init, migrate, upgrade


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RAMS database setup tasks.")
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Ensure base schema exists (create/alter tables as needed).",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Run Flask-Migrate init/migrate/upgrade steps.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run both schema setup and migrations (default).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_all = args.all or (not args.schema and not args.migrate)

    os.environ.setdefault("RAMS_WSGI_SAFE_MODE", "0")

    app = create_app()
    Migrate(app, db)

    with app.app_context():
        if args.schema or run_all:
            print(f"Ensuring database schema at {app.config['SQLALCHEMY_DATABASE_URI']} ...")
            ensure_schema(app, app.logger)
            print("Database schema is up to date.")

        if args.migrate or run_all:
            print("Applying database migrations ...")
            migrations_dir = os.path.join(app.instance_path, "migrations")
            if not os.path.exists(migrations_dir):
                init(directory=migrations_dir)
            migrate(message="Auto migration", directory=migrations_dir)
            upgrade(directory=migrations_dir)
            print("Database migrations are up to date.")


if __name__ == "__main__":
    main()
