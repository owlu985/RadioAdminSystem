# RAMS Feature Snapshot and QoL Opportunities

## Current Feature Coverage (high level)
- **Branding & Navigation**: Global navbar/footer with RAMS branding, station name/slogan, themed dashboard, API docs link, and consistent navigation across admin/public areas.
- **Auth & Roles**: OAuth (Google/Discord) with approval flow, master admin password fallback, custom roles/permissions, and rate-limited endpoints.
- **Scheduling & Recording**: Multi-day shows with multi-DJ support, ShowRun tracking, APScheduler-driven recordings, marathon recorder for 24–48 hour events, and test-mode NAS paths.
- **Detection & Monitoring**: Stream probes with silence/automation/live classification, Icecast listener analytics with ignored IPs, alerts pipeline (simulated until enabled), job self-heal tracking, and dashboard status cards.
- **Logging & Compliance**: Public DJ log submission with autosave and HTML/CSV/DOCX exports, PSA compliance endpoint, log manager/viewer, and ShowRun fallback creation.
- **News & Content**: Flexible news types with upload/rotation scheduling, website content & podcast plugin (API exposed), API docs page, and plugin registry/loader for modular extensions.
- **Music Library & Audits**: NAS search with metadata editing/cue editor, M4A/MP4 tag handling, cover-art harvesting, MusicBrainz enrichment, explicit/audit tooling, waveform/loudness analysis, saved searches, and duplicate/quality queues.
- **DJ Experience**: DJ status page (clock, weather, station status), absence workflow with approvals, DJ profiles (shows, absences, logs, discipline), disciplinary tracking, public schedule grid + iCal, and now/next APIs.
- **Backups & Settings**: Settings export/import, JSON backups for DJs/shows/discipline, configurable bind host/port, alert/webhook/email settings, Tempest weather config, and station background/branding assets.

## QoL Opportunities to Refine Existing Features
- **Dashboard polish**
  - Add quick-filters and search on status/history cards (probes, job health, alerts).
  - Provide inline “last updated” timestamps on each widget to improve operator trust.
  - Offer per-user theme preference (dark/light/system) persisted across sessions.

- **Auth & Roles UX**
  - Surface role/permission badges in the navbar dropdown and on approval queues.
  - Add audit log entries for user approvals/role changes, viewable in admin tools.
  - Provide clearer OAuth setup diagnostics page (tests callbacks, shows detected scopes/domains/guilds).

- **Scheduling & Recording**
  - Add calendar-style schedule editor with drag/drop and conflict warnings.
  - Expose pre-/post-roll buffers per show and surface upcoming-recording countdowns.
  - ShowRun timeline view linking logs, probes, and recording files for rapid triage.

- **Detection & Monitoring**
  - Let admins tune probe thresholds live and run a “test probe” that previews classification.
  - Add debounce/aggregation for alerts with a human-readable incident timeline.
  - Visualize Icecast listener trends with selectable windows (e.g., last 1h/24h/7d) and annotate alert events on the chart.

- **Logging & Compliance**
  - Add inline editing for submitted logs (with audit trail) and bulk export presets.
  - Enable PSA compliance widgets per ShowRun and reminders on the DJ status screen when under target.
  - Provide log templates per show (common PSA/live-read items prefilled for DJs).

- **News & Content**
  - Add staged/published states and version history for news uploads with rollback.
  - Schedule front-page content/podcast entries with future publish/expiry and preview mode.
  - Allow bulk upload of podcast embeds via CSV for faster ingest.

- **Music Library & Audits**
  - Cache waveform/peaks for faster cue editing and add keyboard shortcuts for cue point jumps.
  - Add “fix-it” actions from audit queues (e.g., normalize loudness, embed cover art) with one-click apply.
  - Improve saved searches with sharing per role and quick-pick chips for common filters (missing art, explicit, low bitrate).

- **DJ Experience & Absences**
  - Push absence approvals/changes to the DJ status screen and schedule grid (badges for substitutes).
  - Add optional SMS/Email/Discord notifications to DJs when an absence is approved/denied or coverage is requested.
  - Let DJs attach handoff notes that display during the 15 minutes before/after their show window.

- **Backups & Ops**
  - Expose backup integrity checks and last-success timestamps on Settings.
  - Allow remote backup targets (S3/Wasabi/WebDAV) with signed URL restores.
  - Add retention previews before pruning to avoid accidental data loss.

- **API & Integrations**
  - Add per-endpoint API tokens with scopes/rate limits for website/automation clients.
  - Provide OpenAPI/Swagger export from the API docs page for easier integration.
  - Add a sandbox mode for website-facing endpoints to test widgets without touching live data.

- **Accessibility & Help**
  - Inline help toggles that remember preference and link directly to relevant docs sections.
  - Improve keyboard navigation and focus states for forms/tables, especially on music search and logs.
  - Offer high-contrast theme and configurable font scaling for studio displays.

These suggestions build on current functionality without major architectural changes, aiming to improve operator clarity, reduce clicks, and make integrations smoother for the upcoming website.
