# Studio display and local alerts

The studio display at `/dj/status` combines automation/now-playing information,
the previous two songs, a separate Alerts panel, a daily forecast with high/low
arrows and provider-supplied condition labels, and the next show **today** in
12-hour AM/PM time. Live mode shows the program and host instead of music art.
The dated next-show window uses the configured station timezone, including DST.

## What appears in Alerts

Example (illustrative, not an actual warning):

> **Severe Thunderstorm Warning**
> Putney, Brattleboro, Keene · Until 12:30 PM

Only affected towns appear, up to three, with Putney, Brattleboro and Keene
preferred in that order. An additional-town count indicates wider coverage.
The bulletin description/instructions are never included in the display API.
The highest-priority issue stays pinned; additional issues use the second row.
The station ID reminder appears during a scheduled live show in the last three
minutes before the hour. It is a reminder, not confirmation that an ID aired.

| Code | Display text |
|---|---|
| 02 | NO INPUT DETECTED, CHECK BOARD |
| 04 | RadioDJ handshake failed |
| 07 | Recording storage unavailable |
| 08 | Audio monitoring unavailable |
| 09 | Background monitoring stalled |
| 12 | Barix Error, Restart needed |

NWS failures say **NWS info unavailable**, without a numbered code. Forecast
failures say **Weather unavailable**. No Codes 03, 05, 06, 10 or 11 are added.
Urgent alerts override overnight dimming. The existing optional overnight logo
screensaver is retained; a live show, marathon, active alert or unavailable
monitoring data keeps the status display visible.

## NWS coverage and source handling

The scheduler polls the official NWS active-alert endpoint centrally every 60
seconds using county codes `VTC025` (Windham, VT) and `NHC005` (Cheshire, NH).
County queries include county alerts and intersecting public/fire-zone alerts;
a forecast-zone-only query can miss tornado/thunderstorm warnings and watches.
Browser polling does not trigger NWS collection or encoder recovery.

Town boundaries come from the US Census TIGERweb service. All Windham County
towns are included, together with Bellows Falls village and Keene, Westmoreland,
Walpole, Chesterfield and Hinsdale in New Hampshire. This covers the requested
towns plus Putney's immediate neighboring area. A polygon elsewhere in Cheshire
County is excluded if it reaches none of these towns. For polygon alerts,
towns must have a positive-area intersection with the polygon. Merely being in
the same county does not establish that a town is in a warning.

For geometry-null county/zone products, county membership or cached NWS zone
boundaries determine the affected towns. If a zone boundary is unavailable,
show the short NWS affected-area label rather than invent town names. The six
configured public/fire zone IDs are relevant to the two monitored counties;
review them after NWS zone changes. NWS zone geometries are refreshed weekly.

Only `Actual` alerts are displayed. Tests, cancellation messages and expired
alerts are excluded. Successful collection replaces the active set and
deduplicates alert IDs. A failed check retains only unexpired alerts, marked
as last known, alongside **NWS info unavailable**. Alert polling and restart
state use atomic JSON snapshots and OS file locks shared between app workers.

Official references:

- https://www.weather.gov/documentation/services-web-api
- https://www.weather.gov/documentation/services-web-alerts
- https://www.weather.gov/media/documentation/docs/NWS_Geolocation.pdf

This is a studio information display; NWS CAP input is not connected to EAS
encoder/decoder activation or automatic on-air announcements.

## Barix recovery

One scheduled decoded-playback check equals one sample. HTTP errors (including
4xx), connection failures, non-audio responses, timeouts, premature/empty audio
and decode failures count as failed samples. A playable sample resets the
consecutive-failure count. A silent but valid stream is a **board/input** alert,
not an encoder connection failure.

After `STREAM_DOWN_RESTART_THRESHOLD` consecutive failures (default 3), RAMS
attempts the configured Barix restart only when self-heal and automatic restart
are enabled. Restart requests are separated by a 90-second cooldown. The
existing maximum of three requests per 20-minute window is persisted across
worker/process restarts. Failed requests also consume the limit. An accepted
request means **verifying**, not recovered; only a later playable sample
confirms recovery. If auto-restart is disabled, unconfigured, locked out or
fails, both studio and admin dashboards show **Barix Error, Restart needed**.

The stream probe owns encoder recovery; individual show recorders retry their
recordings without independently restarting the encoder on a local file error.
Admin manual restart remains available at the existing dashboard control.

## Install / configuration

1. Install updated `requirements.txt` in the application's virtual environment
   (adds Shapely for boundary intersections), then restart the RAMS service.
2. Retain a **single scheduler owner** as documented for existing deployment.
   NWS collection starts automatically; no NWS API key is needed.
3. Existing settings control `SELF_HEAL_ENABLED`, `BARIX_AUTO_RESTART_ENABLED`,
   `BARIX_RESTART_URL` / `BARIX_IP`, `STREAM_DOWN_RESTART_THRESHOLD`,
   `BARIX_MAX_RESTARTS_PER_WINDOW` and `BARIX_RESTART_WINDOW_MINUTES`.
   Automatic reboot remains disabled by default; this change does not silently
   enable it on a station that has it disabled.
4. Optional `user_config.json` keys: `NWS_ALERTS_ENABLED`, `NWS_USER_AGENT`,
   `NWS_ALERT_POLL_SECONDS` (default 60, minimum 30),
   `NWS_ALERT_STALE_SECONDS` (default 180), `NWS_ALERT_COUNTIES`,
   `NWS_RELEVANT_ZONES`, `STUDIO_MONITOR_INTERVAL_SECONDS` (default 30),
   `BARIX_RESTART_COOLDOWN_SECONDS` (default 90),
   `MONITOR_STORAGE_MIN_FREE_BYTES` (default 512 MiB), and
   `MONITOR_STORAGE_MOUNT` (set to the actual NAS mount path when applicable).
   Storage checks verify the recording destination and, when supplied, its
   expected mount; they never create a missing mount directory.
5. The service account needs write access to `DATA_ROOT/monitoring`.

Album art requires a recent, recognized RadioDJ AutoDJ handshake and a playing
state or advancing playback metadata. `RDJState` fields are normalized; if the
installed plugin does not report these fields, the display says automation
status unavailable rather than claiming it is active. Validate this on the
studio installation. RadioDJ state alone cannot prove the board fader is up;
the independent decoded-stream/input status remains visible.

Check the actual monitor from the seated desk position after installation.
Native browser checks cover 1920x1080, 1366x768 and a narrow portrait layout;
they cannot establish physical viewing-distance readability.
