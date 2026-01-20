# RAMS Permissions Reference

This table summarizes the built-in roles and the permission scopes that can be granted. Custom roles can be created in Settings, and per-user permission strings can be added in the user management screen.

## Built-in roles and default scopes
| Role | Default scopes |
| --- | --- |
| admin | `*` (all scopes) |
| manager | `schedule:edit`, `music:edit`, `logs:view`, `users:read`, `news:edit`, `audit:run`, `plugins:automation`, `plugins:remote` |
| ops | `schedule:edit`, `logs:view`, `news:edit`, `audit:run`, `plugins:automation`, `plugins:remote` |
| viewer | `logs:view` |

## Scope-to-feature guide
| Scope | Feature areas unlocked |
| --- | --- |
| admin | Full control. Also satisfies any admin-only routes. |
| schedule:edit | Show CRUD, schedule grid, marathon recorder configuration, pause/resume recordings, and schedule exports (including iCal). |
| music:edit | Music search/edit, cue editor, waveform/loudness tools, metadata enrichment (MusicBrainz), cover-art harvesting, duplicate/quality queues, batch/bulk updates. |
| logs:view | Admin log manager (HTML/CSV/DOCX), log exports, compliance views. |
| users:read | User approval/management pages, role assignments, linked OAuth identity management. |
| news:edit | News/PSA uploads, rotation schedules, and news-type configuration. |
| audit:run | Audit tools: recording classification, explicit-content checks, stream/recorder health audits, and related dashboards. |
| plugins:automation | Automation Bridge plugin (rule management, RadioDJ inserts). |
| plugins:remote | Remote Studio Link plugin (session setup/removal). |
| RDJ:read | RadioDJ read-only access (status, playlists, categories, tracks). |
| RDJ:write | RadioDJ write access (playlists, rotations, categories, tracks, quick actions). |

## Notes
- Admin-only routes currently accept users in roles `admin`, `manager`, or `ops`, or any user who has the `admin` scope explicitly assigned.
- Custom scopes entered for a user are merged with their role defaults. Scopes not listed above can be added for future features without code changes, but routes will only honor scopes checked in decorators.
- If you want to restrict a route to a specific scope, add a `permission_required({"<scope>"})` decorator in the view.
