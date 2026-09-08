# Single-Worker WSGI Deployment

RAMS uses its single WSGI process as the owner of scheduled recording,
show-transition detection, and RadioDJ/Icecast metadata jobs. There is no
separate background service to install or supervise.

## Required: exactly one WSGI worker

`wsgi.py` enables scheduler ownership before importing the application. The
WSGI server **must run exactly one process/worker**. Request threads within that
one process are safe; multiple processes would each own a scheduler and could
create duplicate recordings.

Example Apache mod_wsgi configuration:

```apache
WSGIDaemonProcess rams processes=1 threads=5 python-home=/opt/rams/.venv python-path=/opt/rams
WSGIProcessGroup rams
WSGIScriptAlias / /opt/rams/wsgi.py
```

Example Gunicorn command:

```bash
gunicorn --workers 1 --threads 5 --bind 127.0.0.1:5000 wsgi:application
```

Do not use Gunicorn `--preload`, and do not configure more than one daemon
process or worker. Restarting the WSGI process also restarts the one scheduler;
startup reconciliation immediately resumes any show already in progress.

## Startup behavior

The WSGI entrypoint enables safe mode and scheduler ownership before importing
RAMS. Safe mode still avoids running database schema setup, migrations, and
cleanup in the web server, while the explicitly enabled scheduler starts once.
The scheduler:

- creates recording folders and cron jobs for every valid show;
- checks every 15 seconds for a missed transition and starts the remainder;
- pushes show/host metadata when a show is detected;
- uses `SCHEDULE_TIMEZONE` for all show windows; and
- bounds recovery bookkeeping and recorder concurrency to avoid accumulation
  across schedule refreshes.

Run schema setup separately during deployment:

```bash
python scripts/db_setup.py
```

The direct development entrypoint (`python run.py`) also owns one scheduler.
Importing the application through any other entrypoint leaves scheduler startup
disabled unless `RAMS_RUN_SCHEDULER_ON_STARTUP=1` is explicitly set.
