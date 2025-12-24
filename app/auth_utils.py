from functools import wraps
from flask import session, flash, redirect, url_for


ALLOWED_ADMIN_ROLES = {"admin", "manager", "ops"}

ROLE_PERMISSIONS = {
    "admin": {"*"},
    "manager": {"schedule:edit", "music:edit", "logs:view", "users:read", "news:edit", "audit:run"},
    "ops": {"schedule:edit", "logs:view", "news:edit", "audit:run"},
    "viewer": {"logs:view"},
}


def _permissions_from_session():
    perms = set(session.get("permissions") or [])
    role = session.get("role")
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
        if not (_has_role(ALLOWED_ADMIN_ROLES) or _has_permission({"admin"})):
            flash("Please log in to access this page.", "danger")
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)

    return decorated_function


def role_required(roles):
    """Decorator for explicit role checks."""

    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not _has_role(set(roles)):
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for('main.login'))
            return f(*args, **kwargs)

        return wrapped

    return decorator


def permission_required(perms):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                flash("Please log in to access this page.", "danger")
                return redirect(url_for('main.login'))
            if not _has_permission(perms):
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for('main.dashboard'))
            return f(*args, **kwargs)

        return wrapped

    return decorator
