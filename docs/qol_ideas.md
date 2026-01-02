# RAMS Quality-of-Life and Technical Enhancements

This document collects additional ideas to refine RAMS operations, monitoring, and DJ tooling. These are additive and can be implemented incrementally.

## Operations & Reliability
- **Probe auto-heal metrics**: Expand job health to emit per-day success/fail ratios and alert if self-heal loops exceed a limit.
- **Recorder watchdog**: Separate watchdog that verifies recordings are growing on disk during active runs; auto-restarts if growth stalls.
- **Retention simulator**: Preview which files would be deleted under a retention policy before enabling cleanup jobs.
- **Config drift checks**: Periodically validate NAS paths, stream URLs, and OAuth settings; surface drift on the dashboard.

## Monitoring & Alerts
- **Multi-channel alert fan-out**: Add Slack/Teams/webhook alongside Discord/email with per-channel quiet hours.
- **Anomaly detection**: Highlight unusual loudness, missing logs, or probe outliers per show; suggest follow-up actions.
- **Listener analytics**: Trend Icecast listeners by hour/day, with peaks annotated on the dashboard and exportable to CSV.
- **Health/ready endpoints**: `/healthz` (process + DB) and `/readyz` (DB + NAS + stream reachability) for uptime monitors.
- **Incident timelines**: Build a drill-down view showing probes, alerts, listener drops, and recording restarts on a single timeline to speed troubleshooting.

## Scheduling & Show Ops
- **Handoff notes**: DJs leave short notes for the next host; show on the DJ Status screen near show end.
- **Prep checklists**: Per-show reminders (PSAs, contests, promos) surfaced on dashboard and DJ Status.
- **Schedule exceptions**: One-off overrides/blackouts with audit trail and optional notifications to assigned DJs.
- **iCal write-back**: Optional sync to a shared calendar for visibility outside RAMS.

## Music Library & Metadata
- **Batch normalization queue**: Use existing loudness stats to level tracks to a target LUFS and tag results.
- **Cover-art harvesting**: Pull art from MusicBrainz/CoverArt Archive when missing; embed if mutagen is available.
- **Duplicate resolver**: UI to compare suspected dupes (waveform length, hash) and keep/retire/merge metadata.
- **Saved searches**: Persist common filters (e.g., "missing cover art", "low bitrate") for one-click access.
- **Genre/locale clustering**: Build light recommendations to surface related tracks for specialty shows (language, era, genre).
- **Mobile-friendly library view**: Slimmed-down search/detail pages so phones/tablets can review metadata or cue points on-air.

## DJ & Logging Experience
- **In-show reminders**: Time-based prompts for required PSAs/live reads during the slot; log completion with one click.
- **Absence escalation**: Alert Ops if an unfilled absence is approaching show start; suggest backups based on history.
- **Request/CUE bridge**: Lightweight request logger that can push tentative cues to the music editor for quick trimming.
- **Offline cache for logs**: Allow log submission to work offline and sync when back online.

## News & PSA Workflow
- **Version history**: Keep previous news/PSA uploads with quick rollback and diff (timestamp + uploader).
- **Freshness SLAs**: Alerts when news/PSA content exceeds an age threshold; dashboard badge per type.
- **Preflight check**: Validate uploads for duration, silence, and loudness against per-type targets before they enter rotation.

## Compliance & Reporting
- **Run-to-log alignment**: Auto-link recordings to log segments and jump-to-audio for spot checks.
- **PSA pacing reports**: Per-show weekly counts with heatmaps showing where PSAs cluster or are missing.
- **Audit scheduling**: Let audits run on a cadence (e.g., nightly) and deliver summaries via email/Discord.
- **Regulatory exports**: One-click FCC-style affidavit/summary packs (CSV/PDF) combining PSAs, underwriting, and missed shows.

## Theming & UX
- **Dark/light/system theme**: User-selectable with persistence; include high-contrast mode.
- **Keyboard shortcuts**: Quick navigation (e.g., `g s` for schedule, `g m` for music) and table filtering shortcuts.
- **Inline help**: Contextual tips/tooltips in forms (schedule, music edit, logs) with links to docs.

## Integrations & Extensibility
- **Webhooks for track changes**: Push now-playing/track-change events to website/Discord; include CORS-friendly JSON for embeds.
- **Plugin hooks**: Simple extension points for custom imports, audits, or export formats without touching core code.
- **Alternate storage**: Optional S3/Wasabi backend for recordings/logs with signed URL delivery.

## Data Safety & Backups
- **Scheduled backups**: Export DB and critical configs to a backup folder/NAS; optional external push.
- **Restore drills**: One-click dry-run restore to validate backups; report results on the dashboard.

## Listener Experience & Website
- **Public analytics snapshot**: Opt-in public page showing current listeners, recent songs, and top artists over the last 24h.
- **Embeddable widgets**: Copy-paste snippets for now-playing, schedule grid, and DJ bios with optional theme parameters.
- **Song feedback hooks**: Simple thumbs-up/down or “report issue” on now-playing to flag bad metadata or levels.

## Studio & Remote Operations
- **Studio checklist**: Open/close checklist for board ops (mics, automation, encoders, delays) with timestamps and accountability.
- **Remote voice-tracking queue**: Allow pre-recorded breaks to be uploaded/scheduled with per-show permissions and expiration.
- **Equipment logs**: Track gear incidents/maintenance with attachments and reminders for follow-up.
- **Live encoder monitor**: Track encoder uptime/bitrate for each mount, with quick restart links where supported.
- **Remote readiness**: Pre-flight test for remote contributors (mic level, latency, network) with a pass/fail badge before going live.

## Revenue & Underwriting
- **Underwriting log**: Capture underwriter spots separate from PSAs with contract dates and play-count targets.
- **Makegoods tracker**: Flag missed spots and suggest makegood slots; auto-export to PDF/CSV for sponsors.

## Automation & Ingest
- **Hotfolder rules**: Drop-based ingest rules (normalize, retag, route to playlist) with validation before import.
- **Batch retiming**: For long-form content, auto-trim silence and align to target lengths with a preview.

## Intelligence & Insights
- **Show performance reports**: Combine listener peaks, probe health, and log completeness per show/run.
- **Predictive maintenance**: Use job health and disk stats to predict when to rotate hardware/encoders.
