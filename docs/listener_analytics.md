# Listener analytics

Open **Dashboard → Listener analytics and CSV reports**. Set the period, report date and programming filter, then select **View report** or **Download CSV**. Year to date runs through the selected date in the station timezone. Each trend bar represents a period with observations. An empty report means RAMS has no samples for the selection.

## Icecast settings

Set **Icecast Listeners URL** to the authenticated Icecast endpoint ending in `/admin/listclients?mount=/stream`, enter the configured mount and credentials, and select an analytics interval in minutes. The existing default is 15 minutes. A shorter interval captures more brief connections. The dashboard reuses a live count for 30 seconds per web worker. Reports read stored samples and make no Icecast requests.

**Ignored IPs** excludes exact addresses from detailed samples. To exclude the station recorder or stream probe, add its address as seen by Icecast. FFmpeg / Lavf can also represent legitimate relays. RAMS classifies `172.16.0.0/16` and `192.168.0.0/16` as internal. The source IP can belong to a proxy. The reported user agent determines the client label.

## Reading the report

- **Mean concurrent** averages the connection counts in the selected samples. **Peak** is the highest observed count. Client and network averages include zeroes for samples where the category was absent.
- **First-seen connections** counts identified Icecast sessions when first observed. A reconnect can create a new session for the same person. Connections that both start and finish between polls can be missed.
- **Mean connected age** uses Icecast's connected seconds at each observation. Long-running connections can contribute to several samples.
- **Programming** attributes samples to the scheduled show at collection time, accounting for approved uncovered absences and the global recording pause. **Mode** also identifies detected dead air. A scheduled title alone cannot confirm who was in the studio.
- A status-only response supplies the total. Client, network and session summaries use detailed responses. A failed poll creates a gap, and a successful poll with zero listeners records a zero.

The indexed SQLite database sits beside the existing history file at `<ICECAST_ANALYTICS_FILE>.sqlite3`, by default `instance/analytics/icecast_listener_history.jsonl.sqlite3`. Use local writable storage for the database and its SQLite journal files. RAMS imports available JSONL counts once. Existing history has no client or network fields. `ICECAST_ANALYTICS_RETENTION_DAYS` controls the rolling history window (default 365 days, 0 keeps all). Full prior-year comparisons require retention long enough to cover both years. Reports retain aggregate labels and counts. Only the current detailed snapshot's connection IDs and inferred start times are retained for recognizing continuing connections.

## Investigating Code 08

In the master station log, expand the source on a Code 08 row for its reason, consecutive failures and last check/success timestamps. A fresh probe failure records its stage in the stream probe job health entry. Examples include an HTTP status, capture timeout, FFmpeg exit code, short audio sample, or decode error. Two consecutive failed probes raise Code 08. A missing or overdue probe heartbeat raises it independently. A successful decoded sample clears it. Silence in a successfully decoded stream still follows Code 02.
