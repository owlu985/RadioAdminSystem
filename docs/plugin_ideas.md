# Modular Plugin and Feature Ideas for RAMS

Below are suggestions for modular plugins and broader feature expansions that can be layered onto RAMS without disturbing the core app. Plugins are grouped by purpose so teams can pick what to enable.

## On-air and Programming Plugins
- **On-Air Messaging & Overlays**: Manage rotating banner text, sponsor shoutouts, and contest codes; expose a JSON feed the playout system or website widgets can consume.
- **Request & Dedication Inbox**: Public form/API for requests, with moderation queue, on-air notes, and “mark played” hooks; optional Discord relay.
- **Show Assets Pack**: Per-show starter kit (IDs, beds, stingers) with per-episode upload slots; publishable as a ZIP for board ops.

## Music & Library Plugins
- **Third-Party Tag Enrichment**: Fetch genre/mood/BPM/key from services such as AcoustID/AudioDB; stage edits for approval before committing to files.
- **Loudness Normalization Queue**: Batch compute LUFS/peaks, suggest gain, and optionally write ReplayGain/EBU R128 tags.
- **Duplicate/Similarity Finder**: Hash-based duplicates, “same song different edit” detection via duration/ISRC/title heuristics, with one-click merge/archive.
- **Cover Art Harvester**: Pull art from iTunes/Discogs/Spotify (where permitted) and embed; report missing/low-res art.

## Compliance & Logging Plugins
- **PSA/Underwriting Tracker**: Weekly compliance dashboards, grace-period alerts, and export packs (CSV/PDF) for audits.
- **Recording–Log Sync**: Align log entries with recording timestamps, enabling jump-to-audio verification and compliance spot checks.
- **Incident Timeline**: Correlate probes, alerts, listener drops, and automation/live switches for postmortems.

## News & Content Plugins
- **Newsroom Assignments**: Pitch/assignment board with status (pitched/assigned/in edit/published) and deadlines; quick publish to site.
- **Versioned News/PSA Publishing**: Draft/approve/publish with scheduled activation/expiry and rollback history.
- **Website Content Hub**: Extend the existing website/podcast plugin with hero blocks, carousels, and sectioned pages driven by JSON.

## Community & Engagement Plugins
- **Listener Feedback & Polls**: Lightweight surveys tied to shows/runs; summary cards for PDs and DJs.
- **Giveaways/Contests**: Entry forms with eligibility checks, random draw tools, and winner logging.
- **Live Interaction Bridge**: Relay selected requests/shoutouts to the DJ Status screen with acknowledgment buttons.

## Operations & Monitoring Plugins
- **Encoder/Stream Watchdog**: Monitor multiple mounts/encoders, simulate failover, and push alerts to Discord/Email.
- **Disk/Retention Monitor**: Track NAS usage, enforce per-show retention, and forecast capacity with alert thresholds.
- **Job Healthboard**: Visualize scheduled job success/failure/self-heal counts and expose a health API for external monitors.
- **Automation Bridge + RadioDJ inserts**: Rule-based ingest to playout (tags/time-based) with the ability to search RadioDJ by ID/name and inject items at the top of the playlist for urgent cuts or marathons.
- **Low-latency remote studio link**: Browser-based send/return with push-to-mute for field/studio coordination, targeting sub-Discord latency.

## Scheduling & DJ Experience Plugins
- **Sub/Replacement Marketplace**: DJs can claim unstaffed shifts; managers approve and auto-update the schedule/log expectations.
- **Show Prep Checklists**: Per-show prep items (PSAs to read, announcements) visible on the DJ Status screen during the slot.
- **Calendar Sync**: One-way or two-way sync with Google Calendar/ICS for show blocks and special events.

## Archiving & Production Plugins
- **Digitization Queue**: Track tape/vinyl digitization tasks with condition notes, target formats, and checksum verification.
- **Album Rip Assistant**: Semi-automatic track-split helper that uses silence detection and metadata lookup to propose cuts.
- **Spot & Bed Library**: Curated production beds/FX with licensing notes, usage suggestions, and loudness-checked stems.

## Social & Outreach Plugins
- **Multi-Network Poster**: Expand the social console to support scheduled posts, link-in-bio pages, and engagement snapshots.
- **Event Landing Pages**: Microsite generator for concerts/remotes with maps, RSVP, and sponsor highlights driven by plugin config.
- **Webhooks & Embeds**: Pluggable outbound webhooks for now-playing, alerts, and schedule updates; embeddable widgets for sites.

## Security & Access Plugins
- **Granular Permissions**: Custom scopes per plugin feature, role templates, and per-token limits for automation bots.
- **Session Insights**: Admin view of active sessions/devices with revoke controls and optional MFA prompts for privileged actions.

## Backup & Recovery Plugins
- **Config & Data Snapshots**: Scheduled encrypted exports (settings, schedule, DJs, logs, playlists) with retention and restore UI.
- **Disaster Playlists**: Pre-baked “hold music”/emergency rotations that can be triggered from the dashboard or via webhook.

Use these as a menu: enable only what you need, and keep plugin code self-contained (blueprints, templates, static assets, models, and services under a single package) so RAMS stays modular.

## New RAMS Feature Ideas (core)
- **Inline API token manager**: Per-scope API keys with expirations for automation bots.
- **Incident timeline overlay**: Combine probes, alerts, listener drops, and schedule events on one timeline for faster RCA.
- **Studio kiosk mode**: Locked-down dashboards (DJ Status, PSA player, log form) for shared studio PCs with large-type UI.
- **Content freshness guards**: Highlight stale news/PSA assets and prompt owners before expiry windows.

## Plugin Ideas by Department
- **Programming**: “Sub finder” marketplace plugin so open shifts can be claimed; show prep cards surfaced on DJ Status.
- **Production/Creative**: Multi-size promo/export templates with loudness presets; shared palette of approved beds/FX.
- **News**: Assignment board with “ready to air” tags; versioned briefs with diff view; auto-publish to the website plugin.
- **Social**: Scheduled queue with blackout windows and per-network cooldowns; engagement snapshots; link-in-bio generator.
- **Archiving**: Fingerprint-based ingest plus checksum/bit-rot monitoring; digitization queue for tapes/vinyl.
- **Management/Compliance**: PSA pacing heatmaps by daypart; consent/rights tracker for recorded interviews; compliance export bundles.
