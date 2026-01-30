# WSGI Deployment Notes

RAMS ships with startup guards that disable potentially unsafe or expensive initialization work when running under WSGI. The `wsgi.py` entrypoint enables safe mode by default, and you can opt into specific startup tasks using environment flags.

## Startup safety flags

All flags are evaluated on app startup and can be set to `1`, `true`, `yes`, or `on` to enable.

| Flag | Default | Purpose |
| --- | --- | --- |
| `RAMS_WSGI_SAFE_MODE` | `0` (enabled in `wsgi.py`) | When `1`, disables schema setup, migrations, cleanup, and scheduler startup unless explicitly overridden. |
| `RAMS_RUN_SCHEMA_SETUP_ON_STARTUP` | `1` | Run `ensure_schema()` during app startup. |
| `RAMS_RUN_MIGRATIONS_ON_STARTUP` | `1` | Run Flask-Migrate init/migrate/upgrade during app startup. |
| `RAMS_RUN_CLEANUP_ON_STARTUP` | `1` | Delete past shows during startup. |
| `RAMS_RUN_SCHEDULER_ON_STARTUP` | `1` | Start APScheduler jobs during startup. |
| `RAMS_RUN_UTILS_ON_STARTUP` | `1` | Initialize utility helpers during startup. |
| `RAMS_RUN_OAUTH_INIT_ON_STARTUP` | `1` | Initialize OAuth providers during startup. |
| `RAMS_RUN_PLUGIN_LOAD_ON_STARTUP` | `1` | Load plugins during startup. |

When `RAMS_WSGI_SAFE_MODE=1`, the `RUN_SCHEMA_SETUP_ON_STARTUP`, `RUN_MIGRATIONS_ON_STARTUP`, `RUN_CLEANUP_ON_STARTUP`, and `RUN_SCHEDULER_ON_STARTUP` flags are forced off to avoid duplicate work across WSGI workers.

## Running database setup manually

Use the helper script to perform schema setup and migrations outside of app startup:

```bash
python scripts/db_setup.py
```

You can also target just one step:

```bash
python scripts/db_setup.py --schema
python scripts/db_setup.py --migrate
```

This keeps WSGI workers free of heavy startup work while still allowing explicit database setup during deployments.
