from functools import wraps
from flask import session, flash, redirect, url_for


ALLOWED_ADMIN_ROLES = {"admin", "manager", "ops"}


def _has_role(roles):
    if not session.get("authenticated"):
        return False
    if roles is None:
        return True
    current_role = session.get("role")
    return current_role in roles


def admin_required(f):
    """Decorator to require elevated authentication."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _has_role(ALLOWED_ADMIN_ROLES):
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
