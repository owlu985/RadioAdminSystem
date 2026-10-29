# Master Station Log: RadioDJ SQL history

RAMS can read RadioDJ's MySQL/MariaDB `history` table using a separate, read-only
connection. It imports every history row without filtering `song_type`. This does
not depend on the RadioDJ REST plugin or the recent-song widget. SQL capture is
**disabled by default** until the connection is configured.

## Deployment on the existing Ubuntu/Apache installation

Before installing, back up RAMS's application database and `instance/user_config.json`.
Run commands from `/opt/RadioAdminSystem` with the existing virtual environment
(`venv` or `.venv`) activated:

```bash
python -m pip install -r requirements.txt
RAMS_WSGI_SAFE_MODE=1 RAMS_RUN_SCHEDULER_ON_STARTUP=0 python -m flask --app 'app:create_app()' station-log init-db
sudo systemctl restart apache2
```

`init-db` creates only the five new station-log tables; it neither replaces the
RadioDJ database nor alters existing RAMS tables. WSGI safe-mode deployments do
not automatically create these tables. Run the command before opening the viewer.
No live campus database/network configuration is performed by this code change.
Keep the existing single-worker WSGI deployment; the new collector uses that
worker's existing scheduler rather than introducing another scheduler owner.

Open **Shows → Master Station Log → SQL connection & archives** as an account with
`settings:edit`. Configure the database computer's LAN hostname/IP, port, database
name, read-only username/password, and the timezone used by `date_played`.
The default port is 3306; verify the actual MySQL/MariaDB service configuration.
Do not use the REST plugin port. The password is never rendered back into the form;
leaving it blank preserves the current value. Settings persist in the existing
`instance/user_config.json` (saved with owner-only permissions). Restart Apache
after changes so every worker sees them.

For WLMC's mounted NAS, an example destination is:

```text
Archive directory: /mnt/wlmcradio/master_station_logs
NAS mount:         /mnt/wlmcradio
```

Create that directory on the mounted NAS and give RAMS's service account write
access. RAMS refuses exports when the configured mount is absent or the directory
does not exist; it does not create a substitute NAS directory on local disk.
Both fields are configurable. Leaving the archive directory blank disables NAS
exports without disabling database capture or the viewer.

### RadioDJ computer / network prerequisites

Confirm the installed database name and actual service port. The database must
listen on a LAN interface as well as localhost; firewall and campus VLAN rules
must allow the RAMS server to reach it. Restrict access to that server.

Create a separate account with **SELECT on the history table only**. For example,
run equivalent SQL as a database administrator, replacing all placeholders:

```sql
CREATE USER 'rams_history'@'RAMS_SERVER_IP' IDENTIFIED BY 'CHOOSE_A_UNIQUE_PASSWORD';
GRANT SELECT ON `YOUR_RADIODJ_DATABASE`.`history` TO 'rams_history'@'RAMS_SERVER_IP';
```

The importer issues SELECT statements only. It does not update/delete RadioDJ
history, change playout settings, request audio files, or use `LOAD LOCAL INFILE`.
Connections use bounded connect/read/write timeouts and are closed after each run.
A CA file can be supplied through `RADIODJ_SQL_SSL_CA` for verified TLS, including
hostname verification. Use a matching database hostname when TLS is enabled.

If the database authentication method requires PyMySQL's RSA support, install
`PyMySQL[rsa]==1.1.1` in the environment or use verified TLS. Do not change database
users to an unsupported authentication method just to make the connection work.

### Environment / advanced configuration

Values can also be set in `instance/user_config.json`. That file overrides config
class/environment defaults. These names are independent of REST settings:

| Setting | Default / purpose |
| --- | --- |
| `RADIODJ_SQL_ENABLED` | `false`; explicitly enable after configuration |
| `RADIODJ_SQL_HOST`, `RADIODJ_SQL_PORT` | Database computer / `3306` |
| `RADIODJ_SQL_DATABASE` | `radiodj`; confirm installed name |
| `RADIODJ_SQL_USER`, `RADIODJ_SQL_PASSWORD` | Dedicated SELECT account |
| `RADIODJ_SQL_TIMEZONE` | `America/New_York`; verify the database clock |
| `RADIODJ_SQL_SOURCE` | `radiodj-main`; stable identity for one database |
| `RADIODJ_SQL_SSL_CA` | Optional CA path for verified TLS |
| `RADIODJ_SQL_POLL_SECONDS` | `60`; minimum 30 seconds |
| `RADIODJ_SQL_BATCH_SIZE` | `500`; capped at 5000 |
| `RADIODJ_SQL_MAX_BATCHES` | `20` per job; increase for large explicit backfills |
| `RADIODJ_SQL_INITIAL_DAYS` | `7` station calendar days before today |
| `STATION_LOG_ARCHIVE_DIR` | Unset; existing NAS destination |
| `STATION_LOG_ARCHIVE_MOUNT` | Unset; set to the NAS mount to enforce mount checks |
| `STATION_LOG_ARCHIVE_AFTER_DAYS` | `5`; minimum 1 |

Connection fields, enabled flag, source/timezone, CA path and archive paths support
environment variables with these same names. Numeric tuning uses the config file.
On mod_wsgi, arrange environment configuration in the service before Python imports
`config.py`; Apache `SetEnv` alone is not a reliable process-environment substitute.

## Capture and reconciliation

The scheduler imports new history records every minute by default. A persistent
checkpoint and unique `(source, generation, history ID)` prevent duplicate imports.
Each batch and its checkpoint commit in one RAMS transaction. Failed/uncommitted
batches are retried. The first import reads the configured recent window; retained
older days can be backfilled explicitly.

After a successful catch-up, the scheduler rechecks the previous station-local day.
Day queries use an inclusive start and exclusive end. All RAMS event times are
stored as UTC and displayed in the configured station timezone. Station days may
contain 23 or 25 hours at daylight-saving transitions.

A naive RadioDJ SQL timestamp cannot distinguish the two occurrences of a repeated
DST hour. Such rows retain their original value/timezone and are flagged ambiguous;
the first fold is used for ordering. This is evidence uncertainty, not verified
precise airtime. Manual corrections accept explicit ISO offsets.

History deletion/retention remains controlled by RadioDJ. Keep enough history to
cover the longest expected RAMS/network outage plus a margin. The page reports
requested coverage, oldest available history, failed checks, stale capture and
catch-up backlog. Missing source history cannot be reconstructed by this importer.
Neither a successful connection nor an empty day proves complete on-air coverage.

ID regression or a changed checkpoint after a database restore stops collection
and asks for operator review. To explicitly start a new generation:

```bash
RAMS_WSGI_SAFE_MODE=1 RAMS_RUN_SCHEDULER_ON_STARTUP=0 python -m flask --app 'app:create_app()' station-log reset-source --confirm
```

Existing records remain intact. The reset starts capture at the current time to
avoid counting restored history as new plays. Review potential overlap before
explicitly backfilling a restored database. When switching to another database,
choose a new `RADIODJ_SQL_SOURCE`; the settings form enforces this when it detects
a database/host change with an existing checkpoint.

## What a playback entry means

Preserved fields include history ID, track ID, timestamp, song type, subcategory,
genre, title, artist, album, ISRC, duration, original artist, composer, label, year,
track/disc numbers, publisher and copyright. Listener data is excluded.

`reported_duration` is RadioDJ's **reported item length**, not measured broadcast
duration. Missing ISRC stays blank. No title/artist lookup silently assigns one.
RadioDJ history establishes software playback, not that the automation fader was
up or that listeners received that audio. Sweeper coverage remains unverified;
auxiliary players are outside WLMC's current workflow.

## Shows, late submissions and absences

The viewer substitutes programming only inside **explicitly confirmed** show
windows. Engineering events remain visible during shows.
The collector registers today's and yesterday's scheduled windows as unconfirmed,
so a show without any submitted playlist still appears in the review list. These
markers preserve schedule expectations, not proof that the show aired.

- **Aired + playlist:** display the latest DJ submission, with source and revision.
- **Aired + no/empty playlist:** display an explicit missing-playlist block.
- **Missed:** retain RadioDJ playback.
- **Unknown:** show the uncertainty and retain RadioDJ evidence until reviewed.

Submitting the existing public DJ form retains a database revision in addition to
the existing CSV/recording sidecar. It creates an unconfirmed window, never a fake
current-time show run. A submitted/recorded/scheduled show does not prove broadcast.
Review its actual start/end under **Show windows**; account for late starts and
early endings. Confirmed windows cannot overlap. Review actions retain actor,
reason, original values and new values. Public DJ submissions remain labeled
unverified. Operators need `logs:edit` to confirm/correct windows and add notes;
viewing and exports require `logs:view`.
The public playlist form now also accepts an optional ISRC, which is preserved
in both the legacy CSV and the database revision. Older CSVs without that column
remain readable.

Untimed, invalid and out-of-window playlist rows remain visible as needing review;
they are not presented as known exact airtimes. Midnight-spanning entries are placed
on their correct calendar days. A late submission updates its original airing.
A makeup broadcast belongs to its actual later broadcast window, not its original
scheduled slot.

Before this feature, DJ CSVs were the only stored playlist source. They are not
silently scanned from the entire NAS. Import a historical CSV when needed:

```bash
RAMS_WSGI_SAFE_MODE=1 RAMS_RUN_SCHEDULER_ON_STARTUP=0 python -m flask --app 'app:create_app()' station-log import-dj-csv /path/to/show.csv --start 2026-10-28T22:00:00-04:00 --end 2026-10-29T00:00:00-04:00
```

Review the resulting window before substitution. CSV submission failures leave the
legacy CSV intact and show a warning directing the operator to this import command.

## Engineering events and restart evidence

The local event spool preserves queued events independently of the NAS and avoids
committing unrelated request transactions. The scheduler and viewer drain it to
the permanent database; files are removed only after successful inserts. Keep
`DATA_ROOT` on reliable local storage and include it in backups.

Captured sources:

- Existing station code transitions (including silence, monitoring, storage,
  RadioDJ handshake and manual Barix intervention), with raised/cleared events.
- Decoded stream availability/recovery from the existing sampled monitor.
- Barix restart requested and result, including manual/automatic origin, accepted,
  failed, disabled or locked-out outcomes. Acceptance is not confirmed recovery.
- Job failures/self-heal restarts and RAMS logger ERROR/CRITICAL messages.
- RAMS application-process startup and normal process exit; PID retained.
- Operator notes, codes and reported problems, with event time and received time.

Codes are captured at scheduled checks and browser/API observations, with duplicate
refreshes suppressed. A browser can therefore record a stalled-monitor code even
when that monitor cannot run. With no running observer, an independent monitor is
still needed. Silence
and outage times are detection times with sampled uncertainty. An unavailable
stream does not identify the Barix as the failing component. This implementation
does not subscribe to internal Barix SNMP/syslog, detect a physical Barix reboot
performed outside RAMS, or identify exact abrupt RAMS crash times. An independent
monitor/device feed is needed for those observations. Hard kills/power loss may
prevent a shutdown event. Multiple WSGI workers generate separate process events.
Flask management commands are labeled separately from web application startups;
keep the existing single-worker deployment as described in `wsgi_deployment.md`.

## NAS archives and historical reading

After the five-day review window, hourly maintenance writes versioned UTF-8 CSVs:

```text
2026-10-28.r0001.csv
2026-10-28.r0001.sources.csv
```

The compiled CSV uses show substitution; the source CSV retains original playback
and engineering evidence. Stable IDs, UTC/local timestamps with offsets, source,
reported duration, ISRC, severity, codes and supporting JSON details are included.
Potential spreadsheet formulas are escaped. Files are written atomically and
verified before the archive manifest is committed. Failed NAS writes never delete
local events or mark an archive complete. Late information creates a new revision;
older revisions remain available. An unchanged day reuses its existing version.

Database records are retained: the historical viewer does not depend on a NAS scan.
An archived day remains correctable. The page offers current compiled/source views,
search, programming/engineering filters, CSV download and previous archive versions.
The dashboard widget links to the historical viewer and shows today's recent events.

Manual backfill and export examples:

```bash
RAMS_WSGI_SAFE_MODE=1 RAMS_RUN_SCHEDULER_ON_STARTUP=0 python -m flask --app 'app:create_app()' station-log import-history --day 2026-10-28
RAMS_WSGI_SAFE_MODE=1 RAMS_RUN_SCHEDULER_ON_STARTUP=0 python -m flask --app 'app:create_app()' station-log archive --day 2026-10-28
```

Do not treat CSV export as a replacement for database and local-spool backups.
Automatic licensing-report formatting and continuous silence monitoring are not
part of this change.

## Installation acceptance checks

Verify on the installed RadioDJ version:

1. Connection succeeds with the SELECT-only account and the expected schema.
2. A song, PSA, short ID and consecutive replay each produce separate imported rows.
3. Check whether sweepers appear in `history`; document the result.
4. Interrupt the RAMS connection, play several items, restore it and confirm backfill
   without duplicate plays. Check source retention before relying on recovery.
5. Submit a DJ playlist, confirm its actual window, then mark an absence/correct the
   window and verify the compiled view while source playback remains available.
6. Confirm a Barix command outcome is distinct from subsequent decoded recovery.
7. Test a NAS disconnect: events remain readable and exports retry after remount.
