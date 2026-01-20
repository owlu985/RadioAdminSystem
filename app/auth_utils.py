from functools import wraps
from flask import session, abort


ALLOWED_ADMIN_ROLES = {"admin", "manager", "ops"}

PERMISSION_GROUPS = [
    (
        "Dashboard & Access",
        [
            {"key": "dashboard:view", "label": "View dashboard", "description": "Access the main dashboard and status cards."},
            {"key": "users:manage", "label": "Manage users", "description": "Approve accounts, assign roles, and adjust permissions."},
            {"key": "settings:edit", "label": "Edit settings", "description": "Update station settings, alerts, and integrations."},
        ],
    ),
    (
        "Shows & Schedule",
        [
            {"key": "schedule:view", "label": "View schedule", "description": "View show schedule and grid."},
            {"key": "schedule:edit", "label": "Edit schedule", "description": "Create or edit shows and runs."},
            {"key": "schedule:publish", "label": "Publish schedule", "description": "Publish changes to the live schedule grid/iCal."},
            {"key": "schedule:marathon", "label": "Manage marathons", "description": "Create/cancel marathon recorders."},
            {"key": "audit:run", "label": "Run audits", "description": "Access audit tools and history."},
        ],
    ),
    (
        "Music Library",
        [
            {"key": "music:view", "label": "View music library", "description": "Search and view music records."},
            {"key": "music:edit", "label": "Edit metadata", "description": "Edit tags, cues, and artwork."},
            {"key": "music:analyze", "label": "Run analysis", "description": "Trigger audits, waveform/QA jobs."},
        ],
    ),
    (
        "Logs & Compliance",
        [
            {"key": "logs:view", "label": "View logs", "description": "Access log manager and exports."},
            {"key": "logs:edit", "label": "Manage logs", "description": "Edit or delete log sheets and compliance data."},
        ],
    ),
    (
        "News & Content",
        [
            {"key": "news:view", "label": "View news dashboard", "description": "Preview upcoming and archived newscasts."},
            {"key": "news:edit", "label": "Manage news uploads", "description": "Upload or attach news audio/scripts."},
        ],
    ),
    (
        "DJ & Ops",
        [
            {"key": "dj:manage", "label": "Manage DJs", "description": "Edit DJ profiles and assignments."},
            {"key": "dj:discipline", "label": "Disciplinary records", "description": "Add or modify DJ disciplinary entries."},
            {"key": "dj:absence", "label": "Manage absences", "description": "Approve/reject DJ absences."},
        ],
    ),
    (
        "Integrations & Plugins",
        [
            {"key": "plugins:manage", "label": "Manage plugins", "description": "Enable/disable and configure plugins."},
            {"key": "plugins:automation", "label": "Automation bridge", "description": "Use automation bridge tools."},
            {"key": "plugins:remote", "label": "Remote link", "description": "Create or join remote link sessions."},
            {"key": "RDJ:read", "label": "RadioDJ read", "description": "View RadioDJ status, playlists, and library data."},
            {"key": "RDJ:write", "label": "RadioDJ write", "description": "Manage RadioDJ playlists, rotations, categories, and tracks."},
        ],
    ),
    (
        "Social & Alerts",
        [
            {"key": "social:post", "label": "Social posting", "description": "Create and send social posts."},
            {"key": "alerts:manage", "label": "Manage alerts", "description": "Configure alert thresholds and destinations."},
        ],
    ),
]

PERMISSION_LOOKUP = {item["key"]: item for _, items in PERMISSION_GROUPS for item in items}

ROLE_PERMISSIONS = {
    "admin": {"*"},
    "manager": {
        "dashboard:view",
        "schedule:view",
        "schedule:edit",
        "schedule:publish",
        "music:view",
        "music:edit",
        "logs:view",
        "news:view",
        "news:edit",
        "dj:manage",
        "dj:absence",
        "plugins:manage",
        "plugins:automation",
        "plugins:remote",
        "social:post",
        "alerts:manage",
        "settings:edit",
        "users:manage",
    },
    "ops": {
        "dashboard:view",
        "schedule:view",
        "schedule:edit",
        "music:view",
        "logs:view",
        "news:view",
        "news:edit",
        "dj:absence",
        "plugins:automation",
        "plugins:remote",
    },
    "viewer": {"dashboard:view", "schedule:view", "music:view", "logs:view", "news:view"},
}


def _permissions_from_session():
    perms = set(session.get("permissions") or [])
    role = session.get("role")
    perms |= ROLE_PERMISSIONS.get(role, set())
    return perms


def effective_permissions(session_obj=None):
    session_obj = session_obj or session
    perms = set(session_obj.get("permissions") or [])
    role = session_obj.get("role")
    perms |= ROLE_PERMISSIONS.get(role, set())
    return perms


def _has_role(roles):
    if not session.get("authenticated"):
        return False
    if roles is None:
        return True
    current_role = session.get("role")
    return current_role in roles


def _has_permission(required):
    perms = _permissions_from_session()
    if "*" in perms:
        return True
    return bool(perms.intersection(set(required)))


def admin_required(f):
    """Decorator to require elevated authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated"):
            abort(403, description="Requires login")
        if not (_has_role(ALLOWED_ADMIN_ROLES) or _has_permission({"admin"})):
            required_roles = ", ".join(sorted(ALLOWED_ADMIN_ROLES))
            abort(403, description=f"Access required: role in [{required_roles}] or permission: admin")
        return f(*args, **kwargs)

    return decorated_function


def login_required(f):
    """Require an authenticated session without enforcing role."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated"):
            abort(403, description="Requires login")
        return f(*args, **kwargs)

    return decorated_function


def role_required(roles):
    """Decorator for explicit role checks."""

    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                abort(403, description="Requires login")
            if not _has_role(set(roles)):
                role_list = ", ".join(sorted(set(roles)))
                abort(403, description=f"Access required: role in [{role_list}]")
            return f(*args, **kwargs)

        return wrapped

    return decorator


def permission_required(perms):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                abort(403, description="Requires login")
            if not _has_permission(perms):
                perm_list = ", ".join(sorted(perms))
                abort(403, description=f"Access required: permission(s) [{perm_list}]")
            return f(*args, **kwargs)

        return wrapped

    return decorator
