# RAMS Future Feature Concepts

This note captures requested ideas plus a few adjacent suggestions to extend RAMS for station operations. These are planning notes only (no code yet).

## Requested features

### Production director: live-read card generator
- Web form for title, expiration date, and body copy; supports bulk paste of multiple reads separated by a delimiter.
- Generates printable cards (index-card layout) as PDF/Docx with cut guides; auto-sizes fonts to fit; optional QR/code for tracking reads.
- Stores a catalog of live reads with status (active/expired), run count, and show applicability; exportable to CSV for archiving.

### Archivist: music database cross-check
- Import the paid music database when it is converted to spreadsheet/CSV.
- Add a search UI/API to cross-reference album/artist/track/label/UPC; highlight mismatches vs. NAS metadata; support bulk reconciliation tasks.
- Optional “validation session” mode to mark items as verified and export a delta report.

### Social media multi-posting
- Central compose page to post simultaneously to Facebook, Instagram, X, and Bluesky via their APIs.
- Scheduling and per-network content variants (e.g., alt text, character limits, image ratios); store post history with status/URLs.
- Credential vault for per-network tokens; rate-limit safeguards and a dry-run preview showing truncation or missing metadata.

## Additional ideas to consider

- **Incident timeline view**: Combine probe results, alerts, and listener drops on a single timeline to speed troubleshooting.
- **Encoder/stream quality monitor**: Track bitrate/codec deviations and alert if quality drifts from target settings.
- **Retention and cleanup policies**: Per-show retention with disk-usage forecasts; auto-clean older recordings/logs.
- **Track normalization queue**: After loudness analysis, offer batch LUFS normalization and tagging of normalized files (with opt-in apply).
- **PSA/live-read reminders**: Surface required PSAs/reads during each show with completion checkboxes for DJs.
- **Webhooks/embed widgets**: Provide embeddable now-playing/schedule widgets and webhook push for track changes or alerts.

## Notes
- OAuth roles already gate admin areas; public endpoints for DJs (logs, status) remain open by design.
- Requirements such as mutagen/Authlib remain documented in `docs/requirements.md` to keep `requirements.txt` unchanged.
