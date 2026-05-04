# RAMS (Radio Admin Management System) — Technical & User Guide

This guide is intended for both:
- **Beginners** (first-time operators, student staff, volunteer coordinators)
- **Advanced maintainers** (developers, system administrators, engineering leads)

It consolidates setup, architecture, API behavior, permissions, tutorials, and troubleshooting.

---

## 1) What RAMS is

RAMS is a Flask-based station operations platform that combines:
- Show scheduling and recording orchestration
- Stream health monitoring + dead-air/automation detection
- DJ logs and compliance tooling
- Music library search/edit/CUE workflows
- News upload/rotation support
- DJ management and absences
- Audits and analytics
- Extensible plugin modules (automation bridge, remote link, hosted audio, website content)

Key route groups:
- Core web UI: `/...`
- REST-ish APIs: `/api/...`
- Logs workflow: `/logs/...`
- News workflow: `/news/...`
- Plugins: `/plugins/...`

---

## 2) Technical architecture overview

### 2.1 Runtime stack
- **Backend:** Flask
- **Database ORM:** Flask-SQLAlchemy/SQLAlchemy
- **Migrations:** Flask-Migrate/Alembic
- **Scheduling:** APScheduler
- **Audio/tooling:** FFmpeg + pydub + numpy + mutagen integrations

### 2.2 App composition
- `app/__init__.py` builds the app, loads config, enforces optional URL prefixing, initializes DB/migrations, scheduler, logger, OAuth, and blueprints.
- Route modules:
  - `app/routes/dashboard.py` (dashboard/status/schedule feed endpoints)
  - `app/routes/auth.py` (login/logout/OAuth flows)
  - `app/main_routes.py` (major UI/admin flows)
  - `app/routes/logging_api.py` (DJ log submission + management)
  - `app/routes/news.py` (news ingest/config/dashboard)
  - `app/routes/api.py` (programmatic APIs)
- Service layer under `app/services/*` handles integrations and domain logic.

### 2.3 Request flow and URL prefixing
RAMS supports a configured `ADMIN_URL_PREFIX` and rewrites `SCRIPT_NAME/PATH_INFO` so deployment under a subpath works cleanly. All generated links should respect that prefix.

### 2.4 AuthN/AuthZ model
RAMS uses session-based auth and permission scopes:
- Decorators in `app/auth_utils.py`:
  - `login_required`
  - `admin_required`
  - `permission_required({...})`
- Role defaults (`admin`, `manager`, `ops`, `viewer`) merge with per-user explicit scopes.
- `admin` role or `admin` scope can satisfy broad elevated access checks.

---

## 3) Setup instructions (technical)

## 3.1 Prerequisites
- Python 3.10+
- FFmpeg on PATH
- Writable filesystem for instance data, logs, uploads, and (optionally) NAS mounts
- DB backend supported by SQLAlchemy (SQLite commonly used in single-node installs)

## 3.2 Install dependencies
Use repository requirements and install extra packages noted in docs:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install Authlib mutagen
```

## 3.3 Run locally
```bash
export FLASK_APP=run.py
python run.py
```

Or with host/port overrides:
```bash
RAMS_HOST=0.0.0.0 RAMS_PORT=5000 python run.py
```

## 3.4 First-run behavior
On first boot RAMS will:
- Create `instance/` directories as needed
- Generate `instance/user_config.json` with a random `SECRET_KEY` if missing
- Initialize DB connection
- Optionally run schema/setup/migrations depending on startup flags

## 3.5 Production deployment notes
- WSGI entrypoint is available via `wsgi.py`.
- If deployed behind reverse proxy with path prefix, set `ADMIN_URL_PREFIX`.
- Ensure persistent volumes for instance data, recordings, and logs.

---

## 4) API guide: how it works

### 4.1 General behavior
- API blueprint lives under `/api`.
- Most endpoints return JSON payloads.
- Some endpoints support mixed `GET`/`POST` for UI + API workflows.
- Authorization requirements vary by route decorator and session state.

### 4.2 Endpoint catalog

> Note: exact request/response payload fields can evolve; verify quickly in `app/routes/api.py` during upgrades.

#### System / now playing / probe / health
- `GET /api/now`
- `GET /api/now/widget`
- `GET /api/now/recent-tracks`
- `GET|POST /api/now/override`
- `POST /api/probe`
- `GET /api/probes/latest`
- `GET /api/stream/status`
- `GET /api/icecast/analytics`

#### Runs, logs, compliance, reports
- `GET /api/runs/<run_id>`
- `GET /api/runs/<run_id>/logs`
- `GET /api/psa/compliance/<run_id>`
- `GET /api/reports/artist-frequency`

#### Music library, metadata, artwork, enrichment
- `GET /api/music/search`
- `GET|POST|DELETE /api/music/saved-searches`
- `GET /api/music/detail`
- `GET /api/music/cover-image`
- `POST /api/music/cover-art`
- `GET /api/music/cover-art/options`
- `GET /api/music/musicbrainz`
- `GET /api/music/enrich`
- `POST /api/music/bulk-update`
- `GET|POST /api/music/cue`
- `POST /api/music/scan/library`

#### Archivist endpoints
- `GET /api/archivist/album-info`
- `POST /api/archivist/album-rip/upload`
- `POST /api/archivist/album-rip/cleanup`
- `POST /api/archivist/album-rip`
- `GET /api/archivist/musicbrainz-releases`

#### PSA / playback / show automator
- `GET /api/psa/library`
- `GET|POST /api/psa/cue`
- `POST /api/playback/session`
- `GET /api/playback/session`
- `POST /api/playback/session/attach`
- `POST /api/playback/show/start`
- `POST /api/playback/show/stop`
- `POST /api/playback/log`
- `GET /api/playback/queue`
- `POST /api/playback/queue/enqueue`
- `POST /api/playback/queue/dequeue`
- `POST /api/playback/queue/move`
- `POST /api/playback/queue/skip`
- `POST /api/playback/queue/cue`
- `POST /api/playback/queue/fade`
- `POST /api/playback/now-playing`
- `GET /api/show-automator/state`
- `POST /api/show-automator/plan`

#### Schedule, DJs, weather, plugins, indexing, audits
- `GET /api/schedule`
- `GET /api/djs`
- `GET /api/weather/tempest`
- `GET /api/plugins/website/content`
- `GET /api/plugins/website/banner`
- `GET /api/plugins/audio/embed/<item_id>`
- `GET /api/library/index/status`
- `POST /api/library/index/refresh`
- `POST /api/audit/start`
- `GET /api/audit/status/<job_id>`
- `GET /api/audit/runs`
- `GET /api/audit/runs/<run_id>`
- `GET|DELETE /api/audit/runs/<run_id>/results`

#### RadioDJ integration endpoints
- `GET /api/radiodj/psas`
- `GET /api/radiodj/now-playing`
- `POST /api/radiodj/queue`
- `POST /api/radiodj/import/<kind>`
- `PATCH /api/radiodj/psas/<psa_id>/metadata`
- `POST /api/radiodj/psas/<psa_id>/enable`
- `POST /api/radiodj/psas/<psa_id>/disable`
- `DELETE /api/radiodj/psas/<psa_id>`
- `POST /api/radiodj/autodj`

---

## 5) Non-API web routes (operator/admin workflows)

### 5.1 Authentication
- `/login`
- `/login/master`
- `/login/oauth/google` + callback
- `/login/oauth/discord` + callback
- `/login/claim`
- `/login/pending`
- `/logout`

### 5.2 Core operations
- Dashboard/status: `/`, `/dashboard`, `/dj/status`, `/api-docs`
- Schedule views: `/schedule/grid`, `/schedule/ical`, `/shows`, `/show/add`, `/show/edit/<id>`, `/show/delete/<id>`
- Recording controls/data: `/recordings`, `/recordings/download`, `/pause`, `/resume`
- DJs/users/profiles: `/djs`, `/djs/add`, `/users`, `/profile`, `/djs/discipline`, `/absences`
- Logs: `/logs/submit`, `/logs/manage`, `/logs/view`, export routes
- News: `/news/upload`, `/news/dashboard`, `/news/settings`
- Music/admin pages: `/music/search`, `/music/detail`, `/music/edit`, `/music/cue`, `/music/library/editor`
- Settings/admin: `/settings`, `/settings/logs`, `/settings/export`, `/settings/import`
- Plugins pages: `/plugins`, plus plugin-specific routes under `/plugins/...`

---

## 6) Permissions and access control

### 6.1 Built-in roles
- `admin`
- `manager`
- `ops`
- `viewer`

### 6.2 Permission usage model
- Effective permissions = role defaults + per-user explicit permissions.
- `*` wildcard grants full access.
- Route-level checks may require role or explicit scope(s).

### 6.3 Common permission scopes
Examples:
- Scheduling: `schedule:view`, `schedule:edit`, `schedule:publish`
- Music: `music:view`, `music:edit`, `music:analyze`
- Logs: `logs:view`, `logs:edit`
- News: `news:view`, `news:edit`
- DJ/Ops: `dj:manage`, `dj:discipline`, `dj:absence`
- Plugins/Integrations: `plugins:manage`, `plugins:automation`, `plugins:remote`, `RDJ:read`, `RDJ:write`
- Admin domains: `users:manage`, `settings:edit`, `alerts:manage`

---

## 7) Technical tutorials

### Tutorial A — Add a new show and verify schedule publication
1. Log in with account holding `schedule:edit`.
2. Go to **Show Add** page and create show metadata/time.
3. Open schedule grid (`/schedule/grid`) and iCal feed (`/schedule/ical`) to verify publication.
4. Optionally validate via `GET /api/schedule`.

### Tutorial B — Run an audit job and fetch results
1. Open **Audit** page (`/audit`) and start a job.
2. Poll `GET /api/audit/status/<job_id>` until complete.
3. Review list at `GET /api/audit/runs`.
4. Fetch details/results using run-specific endpoints.

### Tutorial C — Music metadata cleanup at scale
1. Search via `/music/search` or `GET /api/music/search`.
2. Inspect detail/cover state (`/music/detail`, `/api/music/detail`, `/api/music/cover-image`).
3. Use bulk endpoint `POST /api/music/bulk-update` for normalized metadata updates.
4. Run CUE adjustments via `/music/cue` or `GET|POST /api/music/cue`.

### Tutorial D — Plugin operations (automation bridge)
1. Ensure plugin enabled in `/plugins`.
2. Configure rule(s) via plugin UI at `/plugins/automation-bridge`.
3. Validate RadioDJ lookups/actions through plugin endpoints and monitor logs.

---

## 8) User guide (beginner-friendly)

## 8.1 Simple first-time setup for non-developers
1. Open RAMS URL in your browser.
2. Click **Login** and sign in with configured method (Google/Discord/master login).
3. If first OAuth login, complete claim form and wait for approval.
4. After approval, log in again and open dashboard.

## 8.2 Registering/approving a user
> Requires account with `users:manage` (or admin-level access).

1. New person signs in and submits **claim** details.
2. Admin visits **Users** (`/users`).
3. Approve user, assign role (viewer/ops/manager/admin), then optional extra permissions.
4. Ask user to log out and back in so session permissions refresh.

## 8.3 Daily DJ workflow (step-by-step)
1. Check **DJ Status** (`/dj/status`) for stream and now/next.
2. Submit show logs at `/logs/submit` during/after show.
3. Use **DJ Tools** (`/dj/tools`) and optional AutoDJ/PSA tools as needed.
4. If absent, submit absence at `/dj/absence`.

## 8.4 News workflow
1. Go to `/news/upload`.
2. Upload audio/script to the correct news type/date.
3. Confirm on `/news/dashboard`.
4. Use `/news/settings` to adjust rotation/config (privileged users).

## 8.5 Music workflow
1. Search at `/music/search`.
2. Open detail and verify tags/artwork.
3. Edit at `/music/edit`; tune CUE points at `/music/cue`.
4. Save and test audio preview if needed.

## 8.6 Permission-safe operating tips
- Give least privilege needed (start with `viewer`/`ops`).
- Use explicit scopes for edge duties rather than broad role escalation.
- Recheck quarterly: remove stale accounts and unnecessary write scopes.

---

## 9) Troubleshooting

### 9.1 Login issues
- Symptom: user stuck on pending.
  - Cause: claim not approved.
  - Fix: admin approves in `/users`.
- Symptom: OAuth callback fails.
  - Cause: redirect URI mismatch.
  - Fix: compare configured callback URLs with provider console settings.

### 9.2 Missing data / broken pages
- Check app logs in `/settings/logs`.
- Validate DB migrations/startup flags.
- Confirm `instance/user_config.json` is readable and valid JSON.

### 9.3 Stream/recording problems
- Ensure FFmpeg is installed and executable.
- Verify stream URL in settings.
- Review probe status on dashboard and `/api/stream/status`.
- Confirm file system permissions for recording directories.

### 9.4 Music metadata edit failures
- Ensure `mutagen` is installed.
- Confirm target files are writable and not locked.
- Validate NAS mount availability and path mapping.

### 9.5 Plugin not visible
- Confirm plugin enabled on `/plugins`.
- Check feature flags/settings required by plugin.
- Review logs for blueprint registration/import errors.

---

## 10) Advanced maintenance checklist

- Back up DB + `instance/` config regularly.
- Export settings before major upgrades.
- Validate APIs after upgrades using `/api-docs` and smoke tests.
- Rotate secrets/tokens when staff changes.
- Audit user permissions monthly.
- Keep FFmpeg and Python dependencies patched.
- Maintain a staging environment for migration testing.

---

## 11) Suggested onboarding plan (handoff-ready)

### Week 1 (operators)
- Login flow, dashboard, DJ logs, absences, news upload.

### Week 2 (admins)
- User approvals, permissions, scheduling, settings export/import.

### Week 3 (technical maintainers)
- API workflows, plugin management, audits, troubleshooting drills.

### Week 4 (hardening)
- Document local SOPs, backup/restore drill, access review, incident simulations.

