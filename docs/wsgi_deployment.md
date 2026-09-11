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
WSGIDaemonProcess rams processes=1 threads=5 maximum-requests=0 python-home=/opt/rams/.venv python-path=/opt/rams
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

Do not configure periodic daemon recycling (for example, a nonzero mod_wsgi
`maximum-requests` or `restart-interval`) on the scheduler-owning process.
Recycling replaces the process that owns APScheduler and any active ffmpeg
recording. The retiring process can otherwise log `cannot schedule new futures
after shutdown` while Python tears down APScheduler's executor, and the new
process can briefly encounter the prior recording during startup catch-up.
Planned deploys should therefore be scheduled around recordings where possible.
RAMS also uses a shutdown-aware APScheduler executor so a final scheduler tick
from a retiring process is discarded rather than logged as a traceback.

`maximum number of running instances reached (1)` is different: it means an
interval elapsed while the preceding invocation was still running. RAMS keeps
`max_instances=1` intentionally so slow RadioDJ, stream, or recording calls do
not overlap. An occasional message is harmless; repeated messages mean the
configured interval is shorter than that external operation's response time.

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

Run schema setup separately **after every code update and before restarting the
web service**. WSGI safe mode deliberately prevents web workers from changing
the database, so deploying a model change without this step leaves the existing
SQLite database unchanged:

```bash
cd /opt/RadioAdminSystem
sudo -u rams env RAMS_WSGI_SAFE_MODE=1 \
  /opt/RadioAdminSystem/venv/bin/python scripts/db_setup.py --all
```

Back up `instance/app.db` before applying schema changes. Use the virtual
environment and service account from the installation; the paths above match a
standard `/opt/RadioAdminSystem` deployment.

If SQLAlchemy reports `no such table: dj_recording_access_code` after installing
the DJ recording portal, the code was updated but this deployment step was not
run against the database used by the service. Stop or restart the service as
appropriate for the deployment, run the command above, and then verify the
table against that same database:

```bash
sudo -u rams sqlite3 /opt/RadioAdminSystem/instance/app.db \
  ".schema dj_recording_access_code"
```

The schema command is idempotent: it preserves existing data and creates tables
that are missing. Do not fix this error by deleting `app.db`, because that would
discard the station's existing data.

The direct development entrypoint (`python run.py`) also owns one scheduler.
Importing the application through any other entrypoint leaves scheduler startup
disabled unless `RAMS_RUN_SCHEDULER_ON_STARTUP=1` is explicitly set.
