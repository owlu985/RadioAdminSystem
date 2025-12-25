# RAMS Feature Overview

This document summarizes the major features available in RAMS, with links to key routes/APIs and brief usage notes.

## Branding, Navigation, and Settings
- Configurable station name/slogan and RAMS branding (logo at `app/static/logo.png`).
- Global navbar/footer on every page with quick links to Dashboard, Schedule, Logs, Music, News, DJs, Audit, API Docs, Settings, and more.
- Settings UI: admin credentials, stream URL, NAS roots, station branding/background, Tempest weather token/station, alert thresholds, OAuth toggles, and JSON import/export for settings.

## Authentication and Roles
- OAuth login with Google and Discord (optional guild/domain restriction).
- Password fallback if enabled; OAuth-only mode available.
- User model with approval flow: new OAuth users submit name, await admin approval, and receive roles (Admin/Manager/Ops or custom roles/per-user permissions). Users can be removed or rejected.

## Dashboard and Status
- Dashboard shows now/next show, stream/probe health (auto-refresh), self-heal counters, quick links to all modules, and alert indicators.
- DJ Status screen (public) with seven-segment clock, stream health, now/next, Tempest weather (current, +1/+2/+4/+8h and 3-day outlook), station logo/background.

## Scheduling and Shows
- CRUD for shows with metadata (name, genre, description, regular host flag, hosts/DJs, timeslots).
- APScheduler-driven recording with silence/automation detection, ShowRun tracking, and missed-show flagging.
- Public schedule grid UI with JSON and iCal feeds for the website.

## Recording, Detection, and Alerts
- Stream probing via FFmpeg/pydub/numpy classifies silence, automation, and live audio; results logged to StreamProbe and ShowRun.
- Self-healing: probe/recorder failures auto-retry; health counters visible on dashboard.
- Dead-air/stream-down alert pipeline (Discord webhook and optional email) with rate-limiting; defaults simulate sending until enabled in Settings.

## Logs and Compliance
- Public DJ log submission (no auth) with required fields, “now” time button, artist suggestions, localStorage autosave, and row add/clear controls.
- Admin log manager with HTML view, CSV/DOCX export, and PSA compliance check (2+ PSA/live reads).
- Logs can be linked to ShowRuns; a fallback ShowRun is created when none is active at submission time.

## News and Community Content
- Flexible news types defined in NAS JSON config; supports daily/weekly rotations and dated uploads.
- News upload page (admin) and scheduler that activates dated files to the correct NAS targets.

## Music Library and Metadata
- NAS search UI/API with detail and edit pages (mutagen-enabled writes), cover art preview, audio preview stream, and RadioDJ-style visual CUE editor with waveform markers.
- Batch/bulk metadata edits; duplicate/quality queues; “recently added,” “needs metadata,” “missing cover art,” and “low bitrate” queues.
- Waveform/loudness analysis and loudness/peak stats for normalization guidance.
- M4A/MP3 tag handling with filename fallback when tags are missing.
- MusicBrainz enrichment endpoint to pull title/artist/album/year/ISRC suggestions for tracks.

## DJs and Bios
- DJ model with bio/photo URL; assign DJs to shows.
- Admin pages to add/edit/list DJs; public API `/api/djs` for website bios and show mappings.

## Audits
- Recording classification audit: scan recordings folder to label live/automation/dead air using the detector.
- Explicit-content audit: checks NAS music via iTunes API for explicit flags/clean versions; rate-limited to avoid 429s; async jobs with progress bars.
- Audit jobs run asynchronously; progress can be viewed without keeping the page open.

## Alerts and Health
- Alert settings for dead-air/stream-down thresholds; simulated sends until enabled.
- Job health tracking for probes/recorders with retry counters and dashboard surfacing.

## APIs
- Now playing: `/api/now`
- Stream status: `/api/stream/status`
- Weather (Tempest): `/api/weather/tempest`
- Probes: `/api/probe`, `/api/probes/latest`
- Runs/logs: `/api/runs/<id>`, `/api/runs/<id>/logs`
- PSA compliance: `/api/psa/compliance/<run_id>`
- RadioDJ PSA controls and imports (enable/disable/delete/update metadata)
- Music search/detail/edit and CUE endpoints
- DJ list: `/api/djs`
- Audit jobs: `/api/audit/start`, `/api/audit/status/<job_id>`
- Schedule grid JSON/iCal feeds
- News upload and config-backed endpoints

## Additional Utilities
- Automatic DB column backfill/DDL patches on startup for new fields.
- Test/NAS mode for offline validation of recording/probe flows.
- API documentation page `/api-docs` linked from the dashboard.
