# Web and Background-Service Deployment

RAMS ships with startup guards that disable potentially unsafe or expensive initialization work when running under WSGI. The `wsgi.py` entrypoint enables safe mode by default, and you can opt into specific startup tasks using environment flags.

## Startup safety flags

All flags are evaluated on app startup and can be set to `1`, `true`, `yes`, or `on` to enable. Scheduler startup now defaults to off: recurring work must run in one supervised background process, not in web workers.

| Flag | Default | Purpose |
| --- | --- | --- |
| `RAMS_WSGI_SAFE_MODE` | `0` (enabled in `wsgi.py`) | When `1`, disables schema setup, migrations, cleanup, and scheduler startup unless explicitly overridden. |
| `RAMS_RUN_SCHEMA_SETUP_ON_STARTUP` | `1` | Run `ensure_schema()` during app startup. |
| `RAMS_RUN_MIGRATIONS_ON_STARTUP` | `1` | Run Flask-Migrate init/migrate/upgrade during app startup. |
| `RAMS_RUN_CLEANUP_ON_STARTUP` | `1` | Delete past shows during startup. |
| `RAMS_RUN_SCHEDULER_ON_STARTUP` | `0` | Legacy/development-only in-process scheduler switch. Keep off for web workers. |
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

## Supervised background service

Start exactly one instance alongside the web application:

```bash
python background_service.py
```

The service owns recording, stream monitoring, RadioDJ/Icecast updates, NAS imports, backups, library indexing, news rotation, and cache cleanup. It reconciles schedule changes made by web workers once per minute and handles `SIGTERM`/`SIGINT` with an orderly APScheduler shutdown.

Example systemd unit (adjust user, group, and paths):

```ini
[Unit]
Description=RAMS background service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rams
Group=rams
WorkingDirectory=/opt/rams
Environment=RAMS_RUN_SCHEDULER_ON_STARTUP=0
ExecStart=/opt/rams/venv/bin/python /opt/rams/background_service.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
```

Do not scale this unit above one replica. Web workers can be scaled independently. During migration, disable `RAMS_RUN_SCHEDULER_ON_STARTUP` in every web process before starting this service to prevent duplicate recordings and maintenance jobs.
